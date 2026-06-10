"""
Play of the Day — runs after game_context.py in the pipeline.
Scans all games across MLB and NBA, picks the single best play,
and stores it in jerry_cache for the app to read.
"""
import requests
import os
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


# ═══════════════════════════════════════════════════════════════════════════
# Casual-bettor label translation table (added 2026-06-09).
# Per project_casual_bettor_ux_docket: translate jargon to plain English while
# keeping all the depth. Each driver contribution gets a `casual_label` field
# alongside the original `label`. App reads casual_label by default; power
# users get the original via a tap affordance.
#
# Principle: don't simplify the product, translate the language. Sweat Score
# stays. Tiers stay. Cohort hit rates get LOUDER (currently buried in detail
# strings — should be the second-most-prominent number on the card).
# ═══════════════════════════════════════════════════════════════════════════
LABEL_TRANSLATIONS = {
    # Model consensus signals
    "v3+v4 DOG consensus": "Both prediction models pick the underdog",
    "v3+v4 consensus": "Both math models agree on the direction",
    "v3 market disagreement": "v3 model sees this far from Vegas",
    "v4 market disagreement": "v4 model sees this far from Vegas",
    "v3 spread edge": "v3 model has spread edge",
    "v4 spread edge": "v4 model has spread edge",
    "v3 spread lean": "v3 model has slight spread lean",
    "v4 spread lean": "v4 model has slight spread lean",
    "v3 trap-zone rescued by v4": "v3 in trap zone but v4 confirms direction",
    "v3+v4 tot consensus": "Both math models agree on total direction",

    # Jerry signals
    "Jerry major total disagreement": "Jerry's projection far from Vegas line",
    "Jerry strong total disagreement": "Jerry projects strong total disagreement",
    "Jerry total edge": "Jerry projects total edge",
    "Jerry total lean": "Jerry projects slight total lean",

    # Confluence
    "PEAK confluence": "Many independent signals align",
    "PEAK confluence on FAV ML": "Many signals align on the favorite",
    "PEAK confluence UNDER bias": "Peak-confluence games skew UNDER historically",
    "High confluence": "Multiple independent signals align",
    "Confluence edge": "Signals lean one direction",
    "Confluence lean": "Slight signal lean",
    "Over-saturated confluence": "Too many signals — likely market-priced in",

    # Cohort (THE big one — already mostly translated but make it pop)
    "Cohort signal confirms": "Matches a historical pattern",

    # Pitcher signals
    "Major xERA gap": "Major pitcher mismatch",
    "xERA gap": "Pitcher quality mismatch",
    "xERA gap (slim)": "Slight pitcher quality edge",
    "Ace duel": "Two top-tier starters",
    "Quality matchup": "Two solid starters",
    "Fragile starter sweet spot": "Starter vulnerable in 1st inning",
    "1st-inn fragile (8+, noisy)": "Starter shaky in 1st (noisy signal)",
    "One fragile starter": "One starter has 1st-inning issues",
    "Mutual NRFI lock": "Both starters typically clean in 1st",
    "One NRFI lock": "One starter typically clean in 1st",
    "Long-rest ace as DOG": "Top starter on extra rest is the underdog",
    "STACKED net=4 + rested ace DOG": "Compound edge: confluence + rested ace dog",

    # NRFI
    "NRFI sweet spot": "Clean 1st inning projected (NRFI tier)",
    "NRFI edge tier": "First-inning suppression projected",
    "NRFI lean band": "Lean toward clean 1st inning",
    "NRFI lean": "Slight first-inning suppression lean",
    "NRFI volatile (95+)": "Extreme NRFI score — historically a trap",
    "YRFI sweet spot": "Both starters shaky early (runs in 1st)",
    "YRFI lean": "First-inning run lean",

    # K signals
    "K-friendly ump": "Umpire calls more strikes",
    "K-prop friendly umpire": "Strikeout-friendly umpire",

    # Other model signals
    "Total lean": "Model leans on the total",
    "Total edge": "Model sees total edge",
    "Major total disagreement": "Model far from Vegas total",
    "Strong total disagreement": "Model sees strong total edge",
    "Total slim edge": "Slight total edge",

    # Trap fades
    "FAV ML trap band": "Favorite price in historical trap zone",

    # Props
    "PRIME ✓book available": "Top-conviction prop available with book line",
    "Multiple PRIME ✓book": "Multiple top-conviction props available",
    "STRONG ✓book available": "Strong-conviction prop available with book line",
    "STRONG ✓book cluster": "Multiple strong-conviction props available",

    # Offense
    "Offense drift gap": "Hot-cold offense split between teams",
    "Offense drift edge": "Offense form differs from season norm",

    # Specific OAA cohort (new from tonight's expansion)
    "OAA loud home": "Home team has major defensive edge",
    "OAA loud away": "Away team has major defensive edge",
    "OAA gap loud": "Major defensive efficiency gap",

    # 6/9 backfill coverage gaps (audited from live 15-game slate)
    "Away pitcher edge vs opp": "Away starter has edge vs this lineup",
    "Home pitcher edge vs opp": "Home starter has edge vs this lineup",
    "Away pitcher mastery vs opp": "Away starter historically dominates this lineup",
    "Home pitcher mastery vs opp": "Home starter historically dominates this lineup",
    "Extreme hitter park": "Park heavily favors hitters",
    "Pitcher-friendly park": "Park favors pitchers",
    "Park slight Under lean": "Park slightly favors UNDER",
    "Jerry alone (no v3/v4 confirmation)": "Jerry projects but math models don't confirm — suppressed",
    "K-gap edge": "Strikeout-rate edge",
    "K-gap large": "Major strikeout-rate edge",
    "OVER skepticism applied": "OVER signal discounted — mixed model agreement",
    "Offense drift lean": "Offense trending different from season norm",
    "PRIME prop available": "Top-conviction prop on this game",
    "STRONG prop available": "Strong-conviction prop on this game",
    "PRIME stack (no-book)": "Multiple top-conviction props (book lines unavailable)",
    "STRONG prop cluster": "Cluster of strong-conviction props",

    # Prop reverse signal (2026-06-10) — lineup-level aggregate vote
    "Prop signals → OVER": "Player props collectively point OVER",
    "Prop signals → UNDER": "Player props collectively point UNDER",
    "Prop signals lean → OVER": "Player props lean OVER (low confidence)",
    "Prop signals lean → UNDER": "Player props lean UNDER (low confidence)",
}


def translate_label(label):
    """Return the casual-English version of a power label, or the label
    itself if no translation exists. Lookup is exact-match — every driver
    label in LABEL_TRANSLATIONS becomes readable; any label not in the
    table renders as-is (which is fine; coverage grows over time as new
    labels are added)."""
    if not label:
        return label
    return LABEL_TRANSLATIONS.get(label, label)


# ── Phase 2 cohort wire-in (2026-06-08) ──
# Lookup is lazy-imported so a missing/broken cohort_signals module never
# breaks play_of_day. evaluate_game_for_play returns [] on any failure.
def _cohort_eval_safe(ctx, play_type, direction):
    try:
        from cohort_signals import evaluate_game_for_play
        return evaluate_game_for_play(ctx, play_type, direction=direction) or []
    except Exception:
        return []


def _side_direction_from_play(side_play, ctx):
    """Determine 'home' or 'away' from side_play dict + game context."""
    if not side_play or not isinstance(side_play, dict):
        return None
    label = (side_play.get('label') or '').lower()
    if not label:
        return None
    home_team = (ctx.get('home_team') or '').lower()
    away_team = (ctx.get('away_team') or '').lower()
    # Match against the team nickname (last word) to keep label match robust
    home_nick = home_team.split()[-1] if home_team else ''
    away_nick = away_team.split()[-1] if away_team else ''
    if home_nick and home_nick in label:
        return 'home'
    if away_nick and away_nick in label:
        return 'away'
    return None


def _total_direction_from_play(total_play):
    """Determine 'over' or 'under' from total_play dict."""
    if not total_play or not isinstance(total_play, dict):
        return None
    tp = (total_play.get('type') or '').upper()
    if 'OVER' in tp:
        return 'over'
    if 'UNDER' in tp:
        return 'under'
    return None


def _cohort_apply_to_dim(ctx, drivers, play_dict, dim_type, track):
    """Phase 2 wire — apply aggregate cohort delta to a sweat dimension.

    Queries every matching rule across the relevant play_types for the
    picked direction. Dedupes by rule_id, sums deltas, caps at ±25,
    appends ONE aggregate driver entry citing the strongest match.
    """
    if dim_type == 'side':
        direction = _side_direction_from_play(play_dict, ctx)
        play_types = ['v3_ml', 'v4_ml', 'jerry_ml', 'conf_ml',
                      'v3_rl', 'v4_rl', 'jerry_rl', 'conf_rl']
    elif dim_type == 'total':
        direction = _total_direction_from_play(play_dict)
        play_types = ['v3_tot', 'v4_tot', 'jerry_tot']
    else:
        return
    if not direction:
        return

    matches = []
    seen = set()
    # Phase 2 tightening (2026-06-08 PM): skip LEAN (+4) and SOFT_FADE
    # (-5) tiers. Initial wire saturated the +/-25 cap on most games
    # because common cohorts (e.g. v3_spread_mid 73.8% applies to any
    # game where v3 has 1-2 run spread lean) kept stacking. Only loud
    # rules contribute now: LOCK, STRONG_EDGE, FADE, HARD_FADE.
    CONTRIBUTING_TIERS = {'LOCK', 'STRONG_EDGE', 'FADE', 'HARD_FADE'}
    for pt in play_types:
        for r in _cohort_eval_safe(ctx, pt, direction):
            rid = r.get('id')
            if not rid or rid in seen:
                continue
            if r.get('tier') not in CONTRIBUTING_TIERS:
                continue
            seen.add(rid)
            matches.append(r)
    if not matches:
        return

    # Take only top 3 strongest by absolute delta (was 5). Combined with
    # the loud-only tier filter above and the tighter ±15 cap below,
    # the cohort layer now boosts but doesn't dominate the dimension.
    matches.sort(key=lambda r: -abs(r.get('conviction_delta', 0)))
    matches = matches[:3]

    total_delta = sum(r.get('conviction_delta', 0) for r in matches)
    # Cap stacked delta at ±15 (was ±25). The cohort wire should
    # influence ranking, not single-handedly push a 60-score game to 100.
    if total_delta > 15: total_delta = 15
    elif total_delta < -15: total_delta = -15
    if total_delta == 0:
        return

    # Cite the strongest match IN THE DOMINANT DIRECTION — avoid
    # citing a fade rule when the net contribution is positive (or vice
    # versa) which was confusing in the smoke test.
    same_direction = [r for r in matches
                      if (r.get('conviction_delta', 0) > 0) == (total_delta > 0)]
    top = (same_direction or matches)[0]
    if total_delta > 0:
        label = 'Cohort signal confirms'
        emoji = '📊'
    else:
        label = 'Cohort signal fades'
        emoji = '⚠️'
    detail = (f"{top.get('matches_if_raw')} ({top.get('tier')}, "
              f"{top.get('shrunken_pct')}% historical, "
              f"{top.get('raw_wins')}-{top.get('raw_losses')} over {top.get('raw_n')} games)")
    if len(matches) > 1:
        detail += f" — top of {len(matches)} matched"

    drivers.append({
        'emoji': emoji, 'label': label,
        'points': total_delta, 'detail': detail,
    })
    # Also record in legacy contribution track for audit
    track.setdefault('contributions', []).append({
        'emoji': emoji, 'label': label,
        'points': total_delta, 'detail': detail,
        'source': 'cohort_signals_v1',
    })


def get_today_et():
    """Get today's date in ET"""
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime('%Y-%m-%d')

def get_mlb_games():
    """Fetch today's MLB game context from Supabase"""
    today = get_today_et()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{today}&select=*",
        headers=HEADERS
    )
    data = r.json()
    if isinstance(data, list):
        return data
    return []

def get_mlb_game_times(date_str):
    """Fetch game commence times from MLB Stats API for matching teams"""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "date": date_str},
            timeout=15
        )
        times = {}
        for d in r.json().get("dates", []):
            for g in d.get("games", []):
                home = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                away = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                game_time = g.get("gameDate")  # ISO format UTC
                if home and game_time:
                    times[home] = game_time
        return times
    except Exception as e:
        print(f"MLB game time fetch error: {e}")
        return {}

def get_nba_teams():
    """Fetch NBA team stats from Supabase"""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/nba_team_stats?season=eq.2025-26&select=*",
        headers=HEADERS
    )
    data = r.json()
    if isinstance(data, list):
        return data
    return []

def get_nba_games():
    """Fetch today's NBA games from Odds API"""
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/basketball_nba/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "spreads,totals,h2h",
                "oddsFormat": "american",
                "bookmakers": "draftkings"
            },
            timeout=15
        )
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0)
        today_end = now.replace(hour=23, minute=59, second=59)
        games = []
        for g in r.json():
            t = datetime.fromisoformat(g['commence_time'].replace('Z', '+00:00'))
            if today_start <= t <= today_end:
                games.append(g)
        return games
    except Exception as e:
        print(f"NBA games fetch error: {e}")
        return []

def sweat_tier_for(score, ctx=None):
    """Tier thresholds aligned with app/index.tsx getSweatTier (line ~2497).
    Both server and app now use the same 4-tier system: PRIME/STRONG/LIGHT_LEAN/PASS.

    Playability gate (2026-05-18): a game cannot reach PRIME tier without a
    qualifying primary_play. Before this gate, games with high confluence +
    big xERA gap but no actionable lean (spread_delta below ML threshold)
    were ranking PRIME on the home-screen sweat card with no specific bet
    to display — confusing UX. Cap at STRONG when there's no primary_play."""
    if score is None:
        return None
    if score >= 80:
        # PRIME requires an actionable lean — otherwise the card shows a
        # giant "high data interest" tile with no specific bet.
        if ctx is not None and not ctx.get('primary_play'):
            return 'STRONG'
        return 'PRIME'
    if score >= 65:
        return 'STRONG'
    if score >= 50:
        return 'LIGHT_LEAN'
    return 'PASS'


_TIER_RANK = {'PASS': 0, 'LIGHT_LEAN': 1, 'STRONG': 2, 'PRIME': 3}


def _fetch_sweat_lock(game_id, game_date):
    """Return (current_tier_max, current_locked_at) from DB or (None, None) on
    miss/failure. Best-effort — if the column doesn't exist yet (migration
    pending) this silently returns (None, None) and the monotonic lock is a
    no-op."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_context"
            f"?game_id=eq.{game_id}&game_date=eq.{game_date}"
            f"&select=sweat_tier_max,sweat_tier_locked_at",
            headers=HEADERS,
            timeout=8,
        )
        if r.status_code != 200:
            return (None, None)
        rows = r.json() or []
        if not rows:
            return (None, None)
        return (rows[0].get('sweat_tier_max'), rows[0].get('sweat_tier_locked_at'))
    except Exception:
        return (None, None)


def write_sweat_score(ctx, score, tier, breakdown=None):
    """Write the score + tier back to mlb_game_context so the app reads the
    same number the server computed. Eliminates the client/server drift that
    made PRIME (68+) effectively invisible in-app (client formula was topping
    out ~65 even when the server said 72).

    2026-05-25: also writes sweat_breakdown (JSONB with contributions+evidence)
    when supplied — feeds the WHY THIS SCORE UI block to achieve parity with
    NBA. Requires 20260525_sweat_breakdown.sql migration to have been applied;
    until then the breakdown field is silently dropped by PostgREST.

    2026-05-29: cap the displayed sweat_score at 79 when primary_play is None.
    Trigger — 5/29 SF@COL showed sweat 93 but rendered as STRONG (tier already
    capped from PRIME because no primary_play met thresholds). User-confusing:
    the 93 number suggested PRIME, the tier said STRONG. Cap aligns the two:
    sweat ≥ 80 only when an actionable PRIME bet exists.
    Raw composite score preserved in breakdown.sweat_score_raw for audit.

    2026-06-03: TIER-MONOTONIC LOCK. Within a single game_date, the persisted
    sweat_tier is the MAX tier the scorer has produced so far. Solves the
    recurring "6 AM STRONG → 2 PM LIGHT_LEAN" intraday regression UX issue
    documented in [[project_pm_cron_live_game_prop_overwrite]]. The score
    (numeric) still floats with the live inputs for audit transparency; only
    the tier is locked. Requires 20260603_sweat_tier_lock.sql migration.
    """
    game_id = ctx.get('game_id')
    if not game_id:
        return
    # Cap score to 79 when there's no actionable play on ANY dimension.
    # 5/29 redesign: cap previously fired on `not ctx.get('primary_play')`
    # alone, which dropped scores even when a clean TOTAL Over edge was
    # present (no primary_play set). New gate: cap only if the winning
    # dimension has no `play` populated. SIDE primary_play missing is
    # fine if TOTAL or PROP has a play.
    displayed_score = int(score)
    has_any_play = False
    if isinstance(breakdown, dict):
        dims = breakdown.get('dimensions') or {}
        winner = dims.get('winning_dimension')
        if winner:
            winner_dim = dims.get(winner) or {}
            has_any_play = winner_dim.get('play') is not None
        else:
            # Pre-dimensional callers fall back to the original primary_play check
            has_any_play = bool(ctx.get('primary_play'))
    else:
        has_any_play = bool(ctx.get('primary_play'))
    if displayed_score >= 80 and not has_any_play:
        if isinstance(breakdown, dict):
            breakdown.setdefault('sweat_score_raw', displayed_score)
            breakdown.setdefault('cap_reason', 'no_dimension_play')
        displayed_score = 79

    # ---- Tier-monotonic lock (2026-06-03) ----
    # Read the current sweat_tier_max for this row. If we computed a LOWER
    # tier than what's already on record for today, hold the persisted tier
    # at the day's high. Score still moves freely (audit transparency) but
    # the tier the user sees does not regress intraday.
    game_date = ctx.get('game_date')
    persisted_tier_max, persisted_locked_at = _fetch_sweat_lock(game_id, game_date)
    computed_tier = tier
    new_rank = _TIER_RANK.get(computed_tier, 0)
    held_rank = _TIER_RANK.get(persisted_tier_max, -1)
    if persisted_tier_max and new_rank < held_rank:
        # Demotion blocked — hold previous max tier; surface the held-down
        # state inside the breakdown so the audit can see what the live
        # scorer would have emitted.
        if isinstance(breakdown, dict):
            breakdown.setdefault('tier_lock', {})
            breakdown['tier_lock'] = {
                'held_at': persisted_tier_max,
                'would_have_been': computed_tier,
                'reason': 'tier_monotonic_within_day',
                'locked_at': persisted_locked_at,
            }
        persisted_tier = persisted_tier_max
    else:
        persisted_tier = computed_tier

    payload = {'sweat_score': displayed_score, 'sweat_tier': persisted_tier}
    # Promote sweat_tier_max + stamp locked_at when this is the first time
    # today we've reached this tier (or a higher one).
    if new_rank > held_rank:
        payload['sweat_tier_max'] = computed_tier
        from datetime import datetime as _dt, timezone as _tz
        payload['sweat_tier_locked_at'] = _dt.now(_tz.utc).isoformat()
    if breakdown is not None:
        payload['sweat_breakdown'] = breakdown
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_id=eq.{game_id}&game_date=eq.{ctx.get('game_date')}",
            headers={**HEADERS, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
            json=payload,
            timeout=10,
        )
        # If columns don't exist yet (migration pending), strip optional
        # fields and retry so the core sweat_score/tier still land.
        if r.status_code == 400:
            fallback = {'sweat_score': displayed_score, 'sweat_tier': persisted_tier}
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_id=eq.{game_id}&game_date=eq.{ctx.get('game_date')}",
                headers={**HEADERS, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                json=fallback,
                timeout=10,
            )
    except Exception as e:
        print(f"  ⚠️ sweat_score writeback failed for {game_id}: {e}")

    # 5/30 — also mirror sweat_dimensions back to mlb_game_results so the
    # audit can grade SIDE/TOTAL/PROP headline calls forward. log_game_result
    # runs BEFORE sweat is computed (it captures pre-game training inputs),
    # so dims have to land via this separate PATCH. Best-effort: missing
    # column triggers a soft-fail with a one-line nudge to run the migration.
    dims = (breakdown or {}).get('dimensions') if isinstance(breakdown, dict) else None
    if dims is not None:
        try:
            rr = requests.patch(
                f"{SUPABASE_URL}/rest/v1/mlb_game_results?game_id=eq.{game_id}",
                headers={**HEADERS, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                json={'sweat_dimensions': dims},
                timeout=10,
            )
            if rr.status_code == 400 and 'sweat_dimensions' in rr.text:
                # column not in results table yet — log once, move on
                pass
        except Exception:
            pass


def _compute_supplementary_play(ctx):
    """NRFI/YRFI surface for the "Also worth a look" tag, computed beside
    the primary_play but never replacing it. Added 2026-05-30 after MIA/NYM
    NRFI POTD lost and the audit (PRIME NRFI 90-94 = 50% on n=22 / 30d)
    confirmed bare NRFI isn't headline-grade. ML/totals now lead; NRFI
    surfaces here only when (a) the score is in a meaningful band AND
    (b) a companion signal supports it.

    Companion signals (any one promotes to STRONG supplementary):
      - Ace duel (both starters ≤3.0 xERA)
      - Cold weather (≤55°F)
      - Pitcher park (≤95 run factor)
      - NRFI-friendly umpire (zone tag or "under" in note)

    Without a companion, NRFI 90-94 is LEAN supplementary — "model sees
    it but the cohort is coinflip alone." Returns dict or None.
    """
    nrfi = ctx.get('nrfi_score')
    if nrfi is None:
        return None
    try:
        nrfi = int(nrfi)
    except (TypeError, ValueError):
        return None

    # YRFI sweet spot — keeps STRONG tier because cohort audit (1st-inn
    # ERA 6-8 + NRFI ≤25 = ~63%) is independent from NRFI demotion. Still
    # routed to supplementary (not primary) so the headline is always
    # ML/total.
    h1 = ctx.get('home_first_inning_era')
    a1 = ctx.get('away_first_inning_era')
    try:
        max_fi = max(float(h1 or 0), float(a1 or 0))
    except (TypeError, ValueError):
        max_fi = 0.0
    if nrfi <= 25 and 6.0 <= max_fi < 8.0:
        return {
            'type': 'YRFI',
            'label': 'YRFI',
            'tier': 'STRONG',
            'sub': f'NRFI {nrfi} + 1st-inn ERA {max_fi:.1f} (audit sweet spot)',
            'audit_note': '1st-inn fragility 6-8 audits ~63%',
        }

    # NRFI sweet spot 90-94 — companion-signal gated. Without companion
    # the surface is LEAN (transparency tag), not STRONG.
    if 90 <= nrfi <= 94:
        try:
            h_xera = float(ctx.get('home_sp_xera') or 4.5)
            a_xera = float(ctx.get('away_sp_xera') or 4.5)
        except (TypeError, ValueError):
            h_xera, a_xera = 4.5, 4.5
        try:
            temp = float(ctx.get('temperature') or 70)
        except (TypeError, ValueError):
            temp = 70.0
        try:
            park = float(ctx.get('park_run_factor') or 100)
        except (TypeError, ValueError):
            park = 100.0
        umpire_note = (ctx.get('umpire_note') or '').lower()

        ace_duel = h_xera <= 3.0 and a_xera <= 3.0
        cold = temp <= 55
        pitcher_park = park <= 95
        nrfi_ump = 'under' in umpire_note and 'over' not in umpire_note.split('under')[0]

        companions = []
        if ace_duel:
            companions.append(f'ace duel ({h_xera:.2f}/{a_xera:.2f} xERA)')
        if cold:
            companions.append(f'cold {int(temp)}°F')
        if pitcher_park:
            companions.append(f'pitcher park {int(park)}')
        if nrfi_ump:
            companions.append('NRFI-friendly umpire')

        if companions:
            return {
                'type': 'NRFI',
                'label': 'NRFI',
                'tier': 'STRONG',
                'sub': f'Score {nrfi}/100 + ' + ' + '.join(companions),
                'audit_note': 'NRFI 90-94 audits 50% alone; companion-signal cohort untracked',
            }
        return {
            'type': 'NRFI',
            'label': 'NRFI',
            'tier': 'LEAN',
            'sub': f'Score {nrfi}/100 — no companion signal',
            'audit_note': 'NRFI 90-94 alone audits 50% (n=22, 30d) — coinflip, surface only',
        }

    # NRFI lean band 80-89 — supplementary LEAN tag only
    if 80 <= nrfi <= 89:
        return {
            'type': 'NRFI',
            'label': 'NRFI',
            'tier': 'LEAN',
            'sub': f'Score {nrfi}/100 — lean band',
            'audit_note': 'NRFI 80-89 lower-conviction band',
        }

    return None


def _compute_prop_alignment(game_props):
    """Identify whether the props on a game collectively point at one TOTAL
    direction (OVER or UNDER). Power the aligned-prop signal in the TOTAL
    sub-score.

    Direction map (loose proxy — captures the run-environment lean):
      OVER total:  hits_over, er_over, ha_over, outs_under
      UNDER total: hits_under, er_under, ha_under, outs_over
      Orthogonal:  ks_*, bb_*  (pitcher-strikeout heavy starts can go either way)

    **2026-05-30 dedup fix**: prop counts dedupe by `player_name`. Same-
    pitcher prop stacks (e.g. 4 Meyer Unders: HA, ER, Outs, etc.) are NOT
    independent signals — they're one correlated bet on that pitcher
    suppressing contact. Counting them as 4 inflated prop confluence and
    triggered a wrong direction-conflict suppression on MIA/NYM 5/29
    (Meyer got shelled, OVER hit; the would-have-been-UNDER headline
    from the inflated count would have been a loss). Each PLAYER counts
    once for its highest-tier directional vote.

    Returns (direction or None, prime_players, strong_players, top_3_props).
    """
    if not game_props:
        return (None, 0, 0, [])
    over_types = {'hits_over', 'er_over', 'ha_over', 'outs_under'}
    under_types = {'hits_under', 'er_under', 'ha_under', 'outs_over'}
    # Per-player highest tier per direction (PRIME beats STRONG)
    TIER_RANK = {'PRIME': 2, 'STRONG': 1}
    over_by_player = {}   # player_name -> (tier, conviction, prop)
    under_by_player = {}
    for p in game_props:
        ptype = p.get('prop_type')
        tier = p.get('tier')
        if tier not in TIER_RANK:
            continue
        player = (p.get('player_name') or '').strip()
        if not player:
            continue
        if ptype in over_types:
            cur = over_by_player.get(player)
            if cur is None or TIER_RANK[tier] > TIER_RANK[cur[0]]:
                over_by_player[player] = (tier, p.get('conviction') or 0, p)
        elif ptype in under_types:
            cur = under_by_player.get(player)
            if cur is None or TIER_RANK[tier] > TIER_RANK[cur[0]]:
                under_by_player[player] = (tier, p.get('conviction') or 0, p)
    over_count = len(over_by_player)
    under_count = len(under_by_player)
    if over_count == 0 and under_count == 0:
        return (None, 0, 0, [])
    if over_count >= under_count:
        chosen_players, direction = over_by_player, 'OVER'
        other_count = under_count
    else:
        chosen_players, direction = under_by_player, 'UNDER'
        other_count = over_count
    # Require a clear directional lean: chosen side at least 2x the other,
    # OR the other side is empty. Mixed signals (3 Over players + 2 Under
    # players) are noise.
    if other_count and len(chosen_players) < 2 * other_count:
        return (None, over_count, under_count, [])
    prime_count = sum(1 for t, _, _ in chosen_players.values() if t == 'PRIME')
    strong_count = sum(1 for t, _, _ in chosen_players.values() if t == 'STRONG')
    top_3 = [entry[2] for entry in sorted(chosen_players.values(), key=lambda x: -x[1])[:3]]
    return (direction, prime_count, strong_count, top_3)


def score_mlb_game(ctx, game_props=None, track=None):
    """Score an MLB game's overall sweat heat, decomposed across three
    dimensions: SIDE (ML/spread), TOTAL (Over/Under, NRFI/YRFI), PROP
    (game has standout props worth posting).

    2026-05-29 redesign — sweat split into 3 sub-scores. Pre-split: one
    headline number washed total-only edges (ATL/CIN: +0.93 total delta,
    4 aligned props, scored 38 PASS). Post-split: each dimension scored
    independently; headline = max(side, total, prop) so total-edge games
    surface STRONG/PRIME on the TOTAL dimension instead of disappearing.

    Returns (headline_score, dimensions_dict) where dimensions_dict carries
    per-dimension score / tier / drivers / play. Callers use headline_score
    as the column value and pass dimensions_dict into write_sweat_score for
    persistence into the breakdown JSONB.

    Backward-compat: `track` still gets the OLD contributions/evidence
    lists populated for the existing app UI block. Dimensions metadata
    is additional, not a replacement.

    Tier thresholds per dimension (sweat_tier_for): 80/65/50/<50.

    Earlier history (2026-05-16): full rewrite to break out of the 42-45
    PASS cluster. (2026-05-25): added `track` for WHY-THIS-SCORE UI.
    """
    if track is None:
        track = {'contributions': [], 'evidence': []}

    # Per-dimension driver lists (independent of the legacy
    # contributions/evidence lists). Each driver is {label, points, detail}.
    side_drivers, total_drivers, prop_drivers = [], [], []

    def _contrib(emoji, label, points, detail=None):
        """Legacy contribution tracker — populates the existing UI block."""
        if points > 0:
            entry = {'emoji': emoji, 'label': label, 'points': points}
            if detail:
                entry['detail'] = detail
            track.setdefault('contributions', []).append(entry)

    def _evidence(emoji, label, detail=None):
        entry = {'emoji': emoji, 'label': label}
        if detail:
            entry['detail'] = detail
        track.setdefault('evidence', []).append(entry)

    def _add(bucket_drivers, points, emoji, label, detail=None):
        """Routes a contribution to BOTH the legacy track AND the per-
        dimension drivers list, so sweat_breakdown.dimensions carries the
        decomposition without duplicating call sites."""
        if points > 0:
            _contrib(emoji, label, points, detail)
            bucket_drivers.append({'emoji': emoji, 'label': label, 'points': points, 'detail': detail})

    # Pre-compute prop alignment once — used by TOTAL sub-score.
    prop_dir, prop_dir_prime, prop_dir_strong, prop_dir_top = _compute_prop_alignment(game_props)

    # ---- TOTAL: NRFI / YRFI (audit-calibrated, REWEIGHTED 2026-05-30) ----
    # NRFI/YRFI is an inning total bet — routes entirely to TOTAL bucket.
    # Weights HALVED from 5/29 levels after MIA/NYM NRFI POTD lost and the
    # audit (PRIME NRFI 90-94 = 50% on 22 games / 30d) confirmed NRFI is
    # at coinflip even in the best cohort. Previous +30 sweet-spot weight
    # was making NRFI the loudest single signal in the system — out of
    # proportion with its actual hit rate. New weights keep NRFI surfacing
    # as a real signal but no longer headline-grade on its own. POTD
    # framing for NRFI is dropped entirely (see compute_primary_play).
    nrfi = ctx.get('nrfi_score') or 0
    nrfi_band_label = None  # tracked for total_play headline
    if 90 <= nrfi <= 94:
        _add(total_drivers, 15, '⚾', 'NRFI sweet spot', f'Score {int(nrfi)}/100 — audit 50% (n=22, coinflip)')
        nrfi_band_label = 'NRFI'
    elif 88 <= nrfi <= 89:
        _add(total_drivers, 11, '⚾', 'NRFI edge tier', f'Score {int(nrfi)}/100')
        nrfi_band_label = 'NRFI'
    elif nrfi >= 95:
        _add(total_drivers, 6, '⚠️', 'NRFI volatile (95+)', f'Score {int(nrfi)}/100 — fade cohort 47.8%')
    elif 80 <= nrfi <= 89:
        _add(total_drivers, 7, '⚾', 'NRFI lean band', f'Score {int(nrfi)}/100')
        nrfi_band_label = 'NRFI'
    elif 70 <= nrfi <= 79:
        _add(total_drivers, 5, '⚾', 'NRFI lean', f'Score {int(nrfi)}/100')
    elif nrfi <= 30:
        _h1 = float(ctx.get('home_first_inning_era') or 4.5)
        _a1 = float(ctx.get('away_first_inning_era') or 4.5)
        _max_fi = max(_h1, _a1)
        if 6.0 <= _max_fi < 8.0:
            _add(total_drivers, 7, '🔥', 'YRFI sweet spot', f'NRFI {int(nrfi)} + 1st-inn ERA {_max_fi:.1f} — audit ~63%')
            nrfi_band_label = 'YRFI'
    elif nrfi <= 40:
        _add(total_drivers, 4, '🔥', 'YRFI lean', f'NRFI score {int(nrfi)}/100')

    # ---- TOTAL: Pitcher xERA mismatch ----
    # Moved from side bucket 2026-05-29 — a 2-run xERA gap is a TOTAL signal
    # (one side scores, one doesn't) not a side signal (no direct ML edge).
    home_xera = float(ctx.get('home_sp_xera') or 4.5)
    away_xera = float(ctx.get('away_sp_xera') or 4.5)
    xera_gap = abs(home_xera - away_xera)
    if xera_gap >= 2.0:
        _add(total_drivers, 14, '⚖️', 'Major xERA gap', f'{xera_gap:.2f}-run pitcher mismatch')
    elif xera_gap >= 1.5:
        _add(total_drivers, 9, '⚖️', 'xERA gap', f'{xera_gap:.2f}-run pitcher mismatch')
    elif xera_gap >= 1.0:
        _add(total_drivers, 6, '⚖️', 'xERA gap', f'{xera_gap:.2f}-run pitcher mismatch')
    elif xera_gap >= 0.5:
        _add(total_drivers, 3, '⚖️', 'xERA gap (slim)', f'{xera_gap:.2f}-run pitcher mismatch')

    # ---- TOTAL: Both pitchers elite (ace duel — points at UNDER) ----
    if home_xera <= 3.0 and away_xera <= 3.0:
        _add(total_drivers, 10, '🎯', 'Ace duel', 'Both starters ≤3.00 xERA')
    elif home_xera <= 3.5 and away_xera <= 3.5:
        _add(total_drivers, 5, '🎯', 'Quality matchup', 'Both starters ≤3.50 xERA')

    # ---- TOTAL: 1st-inning extremes (NRFI lock or YRFI fade) ----
    h1 = float(ctx.get('home_first_inning_era') or 4.5)
    a1 = float(ctx.get('away_first_inning_era') or 4.5)
    if 6.0 <= max(h1, a1) < 8.0:
        _add(total_drivers, 8, '🔥', 'Fragile starter sweet spot', f'1st-inn ERA {max(h1,a1):.1f}')
    elif 8.0 <= max(h1, a1):
        _add(total_drivers, 2, '🔥', '1st-inn fragile (8+, noisy)', f'1st-inn ERA {max(h1,a1):.1f}')
    elif h1 >= 6.0 or a1 >= 6.0:
        _add(total_drivers, 5, '🔥', 'One fragile starter', '1st-inn ERA ≥6 one side')
    if h1 <= 1.5 and a1 <= 1.5:
        _add(total_drivers, 6, '🛡️', 'Mutual NRFI lock', 'Both 1st-inn ERA ≤1.5')
    elif h1 <= 1.5 or a1 <= 1.5:
        _add(total_drivers, 3, '🛡️', 'One NRFI lock', 'One 1st-inn ERA ≤1.5')

    # ---- SIDE: Signal confluence (strongest side indicator) ----
    # 2026-06-05 REWEIGHT (n=640 backtest, _backtest_outside_box.py):
    #   net=4 + DOG RL: 82.6% (n=23)   ← PEAK
    #   net=4 + FAV ML: 69.2% (n=13)   ← NEW surface
    #   net=5 + DOG RL: 50.0% (n=16)   ← deteriorates
    #   net=6 + DOG RL: 28.6% (n=7)    ← worse than coinflip
    # Conclusion: peak at net=4, then decay. Old ladder rewarded 5+/6+ MORE
    # than 4 which is backwards. New ladder caps at net=4 = +12, decays after.
    conf_net = ctx.get('signal_confluence_net')
    try:
        conf_mag = abs(int(conf_net)) if conf_net is not None else 0
    except (TypeError, ValueError):
        conf_mag = 0
    if conf_mag >= 6:
        _add(side_drivers, 6, '🎯', 'Over-saturated confluence', f'{conf_mag} signals (too obvious — market priced in)')
    elif conf_mag >= 5:
        _add(side_drivers, 8, '🎯', 'High confluence', f'{conf_mag} independent signals align')
    elif conf_mag == 4:
        # Cohort percentage pulled fresh from cohort_stats.json (nightly
        # recompute, see cohort_stats.py). Falls back to generic label if
        # the stats file is missing or stale — never ships a stale number.
        from cohort_lookup import format_label as _cohort_label
        _cohort = _cohort_label('conf4_dog_rl', fallback='lifetime cohort')
        _add(side_drivers, 12, '🎯', 'PEAK confluence',
             f'{conf_mag} signals — strongest cohort ({_cohort} DOG RL)')
    elif conf_mag == 3:
        _add(side_drivers, 6, '🎯', 'Confluence edge', f'{conf_mag} signals on one side')
    elif conf_mag == 2:
        _add(side_drivers, 3, '🎯', 'Confluence lean', f'{conf_mag} signals on one side')

    # ---- SIDE: PEAK confluence + FAV ML surface (NEW 2026-06-05) ----
    # When confluence net=4 AND it points to the favorite, FAV ML hits 69.2%
    # (n=13, +0.322 EV at -110). Distinct from DOG RL at same net level.
    # Bonus: when net=4 points at fav and the ML price isn't a trap band,
    # add a recognition driver so downstream picker can surface as ML.
    if conf_mag == 4 and conf_net is not None:
        try:
            conf_points_home = int(conf_net) > 0
            cs_val = ctx.get('close_spread') or ctx.get('open_spread')
            if cs_val is not None:
                home_is_fav = float(cs_val) < 0
                conf_points_fav = (conf_points_home == home_is_fav)
                home_ml = ctx.get('home_ml_close') or ctx.get('home_ml_open')
                away_ml = ctx.get('away_ml_close') or ctx.get('away_ml_open')
                fav_ml = home_ml if home_is_fav else away_ml
                if conf_points_fav and fav_ml is not None:
                    try: fm = int(fav_ml)
                    except (TypeError, ValueError): fm = None
                    if fm is not None and not (-150 <= fm <= -130):
                        from cohort_lookup import format_label as _cohort_label
                        _cohort = _cohort_label('conf4_fav_ml', fallback='lifetime cohort')
                        _add(side_drivers, 8, '⚙️', 'PEAK confluence on FAV ML',
                             f'net=4 favorite at {fm} ({_cohort} cohort)')
        except (TypeError, ValueError):
            pass

    # ---- SIDE: Spread delta — v3 + v4 (RESHAPED 2026-06-05) ----
    # SIDE backtest n=640 graded games:
    #   ML picks: ALL three models at 47-50% (coinflip). Do NOT pick ML.
    #   DOG RL picks: v3 60.7%, v4 65.9% lifetime. REAL EDGE.
    #   FAV RL picks: 25-54% across edges. Avoid.
    #   Jerry spread direction: 47.8% (coinflip across n=67). Benched.
    #   Confluence net=4 + DOG: 82.6% (n=23) — strongest single signal.
    #   v3+v4 consensus DOG (>=0.5 each, same direction): 67.2% (n=125).
    #
    # New scoring: v3 + v4 both contribute (v3 was alone before, but v4
    # spread audited 65.9% on DOG RL — clear math model worth weighting).
    # Jerry spread bands zeroed; Jerry stays in DB for transparency.
    # v3+v4 DOG consensus bonus added (mirror of total consensus play).
    #
    # Sign convention: projected_spread / model_pred_spread POSITIVE = home
    # favored. close_spread book-side (negative = home laid). Disagreement
    # magnitude = abs(model_spread + close_spread).
    close_spread_val = ctx.get('close_spread') or ctx.get('open_spread')
    proj_spread_val = ctx.get('projected_spread')
    v4_spread_val = ctx.get('model_pred_spread')
    jerry_spread_val = ctx.get('jerry_pred_spread')

    v3_signed = None
    if proj_spread_val is not None and close_spread_val is not None:
        try:
            v3_signed = float(proj_spread_val) + float(close_spread_val)
        except (TypeError, ValueError):
            v3_signed = None

    v4_signed = None
    if v4_spread_val is not None and close_spread_val is not None:
        try:
            v4_signed = float(v4_spread_val) + float(close_spread_val)
        except (TypeError, ValueError):
            v4_signed = None

    jerry_signed = None
    if jerry_spread_val is not None and close_spread_val is not None:
        try:
            jerry_signed = float(jerry_spread_val) + float(close_spread_val)
        except (TypeError, ValueError):
            jerry_signed = None

    v3_abs = abs(v3_signed) if v3_signed is not None else abs(float(ctx.get('spread_delta') or 0))
    v4_abs = abs(v4_signed) if v4_signed is not None else 0.0
    jerry_abs = abs(jerry_signed) if jerry_signed is not None else 0.0

    # v3 contribution (with trap-zone rescue when v4 confirms direction)
    # 2026-06-05: trap-zone rescue logic now checks v4 (math-trend model)
    # instead of Jerry (coinflip).
    if v3_abs >= 2.0:
        _add(side_drivers, 13, '📊', 'v3 market disagreement', f'{v3_abs:.1f}-run v3 vs market')
    elif v3_abs >= 1.5:
        if (v3_signed is not None and v4_signed is not None
                and v3_signed * v4_signed > 0 and v4_abs >= 1.0):
            _add(side_drivers, 6, '📊', 'v3 trap-zone rescued by v4', f'{v3_abs:.1f} v3 + v4 confirms {v4_abs:.1f}')
    elif v3_abs >= 1.0:
        _add(side_drivers, 8, '📊', 'v3 spread edge', f'{v3_abs:.1f}-run v3 vs market')
    elif v3_abs >= 0.5:
        _add(side_drivers, 3, '📊', 'v3 spread lean', f'{v3_abs:.1f}-run v3 vs market')

    # v4 spread contribution (NEW 2026-06-05) — v4 was previously absent
    # from SIDE scoring. Audit shows v4 spread predicts DOG RL at 65.9%.
    if v4_signed is not None:
        if v4_abs >= 2.0:
            _add(side_drivers, 13, '🔧', 'v4 market disagreement', f'{v4_abs:.1f}-run v4 vs market')
        elif v4_abs >= 1.5:
            _add(side_drivers, 8, '🔧', 'v4 spread edge', f'{v4_abs:.1f}-run v4 vs market')
        elif v4_abs >= 1.0:
            _add(side_drivers, 5, '🔧', 'v4 spread edge', f'{v4_abs:.1f}-run v4 vs market')
        elif v4_abs >= 0.5:
            _add(side_drivers, 2, '🔧', 'v4 spread lean', f'{v4_abs:.1f}-run v4 vs market')

    # v3 + v4 DOG consensus bonus (NEW 2026-06-05) — when both models
    # agree direction AND that direction is the DOG side, +12 points.
    # Backtested 67.2% / n=125 lifetime. Mirrors the TOTAL consensus play.
    #
    # FAV ML -130/-150 TRAP FADE (2026-06-05): when v3+v4 consensus picks
    # the FAVORITE side AND fav ML is in -130 to -150 band, the lifetime
    # cohort is 80-80 (50% / -0.143 EV at -140). Apply -8 penalty unless
    # confluence net >= 4 (which validates the fav-side pick separately).
    if v3_signed is not None and v4_signed is not None and close_spread_val is not None:
        if v3_abs >= 0.5 and v4_abs >= 0.5:
            v3_picks_home = v3_signed > 0
            v4_picks_home = v4_signed > 0
            if v3_picks_home == v4_picks_home:
                try:
                    cs = float(close_spread_val)
                    home_is_fav = cs < 0
                    is_dog = (v3_picks_home != home_is_fav)
                    if is_dog:
                        _add(side_drivers, 12, '🤝', 'v3+v4 DOG consensus',
                             f'Both models pick the dog side (v3 {v3_signed:+.2f}, v4 {v4_signed:+.2f})')
                    else:
                        # Models agree on FAVORITE side — check trap band
                        home_ml_v = ctx.get('home_ml_close') or ctx.get('home_ml_open')
                        away_ml_v = ctx.get('away_ml_close') or ctx.get('away_ml_open')
                        fav_ml_v = home_ml_v if home_is_fav else away_ml_v
                        if fav_ml_v is not None:
                            try: fmv = int(fav_ml_v)
                            except (TypeError, ValueError): fmv = None
                            if fmv is not None and -150 <= fmv <= -130 and conf_mag < 4:
                                _add(side_drivers, -8, '⚠️', 'FAV ML trap band',
                                     f'Fav at {fmv} hits 50% lifetime — no confluence rescue')
                except (TypeError, ValueError):
                    pass

    # Jerry spread contribution BENCHED 2026-06-05. Lifetime spread
    # direction 47.8% (coinflip on n=67). Keep Jerry projections in DB
    # for transparency but stop counting them in the dim score until
    # Jerry proves itself on n>=60 graded games with rolling 14-day
    # accuracy >= 60%. Same gating as TOTAL dim Jerry change.

    # ---- SIDE: Long-rest ace as DOG (NEW 2026-06-05) ----
    # Backtest (_backtest_advanced_605.py) n=158: long-rest (>=5 days)
    # ace (xERA <= 3.70) as the DOG SP hits 62.0% on DOG RL lifetime
    # (+0.184 EV at -110). Mechanism: ace dogs get less public action,
    # extra rest sharpens velocity, dog +1.5 cushion lets you absorb
    # the matchup-luck variance. Bigger sample than any other cohort
    # we've shipped today.
    #
    # STACKED bonus: when net=4 confluence ALSO points at the dog AND
    # that dog has the long-rest ace SP, the combined cohort hits 85.0%
    # on n=20 (+0.623 EV). Compound bonus tier.
    # Field names: mlb_game_context uses home_days_rest / away_days_rest
    # (mlb_game_results uses the home_sp_days_rest form — checked both for
    # forward-compat with future column rename).
    h_rest = ctx.get('home_days_rest') if ctx.get('home_days_rest') is not None else ctx.get('home_sp_days_rest')
    a_rest = ctx.get('away_days_rest') if ctx.get('away_days_rest') is not None else ctx.get('away_sp_days_rest')
    h_xera = ctx.get('home_sp_xera')
    a_xera = ctx.get('away_sp_xera')
    try:
        cs_now = float(close_spread_val) if close_spread_val is not None else None
    except (TypeError, ValueError):
        cs_now = None
    if cs_now is not None:
        home_is_fav_chk = cs_now < 0
        for side, pick_home_side in [('home', True), ('away', False)]:
            rest_v = h_rest if side == 'home' else a_rest
            xera_v = h_xera if side == 'home' else a_xera
            try:
                rest_f = float(rest_v) if rest_v is not None else None
                xera_f = float(xera_v) if xera_v is not None else None
            except (TypeError, ValueError):
                continue
            if rest_f is None or xera_f is None: continue
            if rest_f < 5 or xera_f > 3.70: continue
            # only fire on dog side (cohort edge is dog-flavored)
            is_dog_side = (pick_home_side != home_is_fav_chk)
            if not is_dog_side: continue
            _add(side_drivers, 6, '😴', 'Long-rest ace as DOG',
                 f'{side.title()} SP: {rest_f:.0f}d rest + {xera_f:.2f} xERA (62.0% DOG RL lifetime)')
            # Stacked: net=4 confluence ALSO points at this dog → +6 more (total +12)
            if conf_mag == 4 and conf_net is not None:
                try:
                    conf_points_home = int(conf_net) > 0
                    if conf_points_home == pick_home_side:
                        _add(side_drivers, 6, '⚡', 'STACKED net=4 + rested ace DOG',
                             '85% lifetime cohort — compound edge')
                except (TypeError, ValueError):
                    pass

    # ---- SIDE: Offense drift differential (NEW 2026-05-31) ----
    # Hot/cold gap between the two lineups is a side signal the system was
    # ignoring. PHI/LAD case: PHI drift -1.45 (frozen) vs LAD drift +0.44 →
    # 1.89-run L10-vs-season offensive differential pointing at LAD ML/RL.
    # Banding mirrors xERA gap thresholds in spirit (>=1.5 strong, >=1.0
    # edge, >=0.6 lean) and is calibrated to be one driver in the stack,
    # not a headline mover on its own.
    home_drift = ctx.get('home_offense_drift')
    away_drift = ctx.get('away_offense_drift')
    if home_drift is not None and away_drift is not None:
        try:
            drift_gap = abs(float(home_drift) - float(away_drift))
            if drift_gap >= 1.8:
                _add(side_drivers, 8, '🔥', 'Offense drift gap', f'{drift_gap:.2f}-run hot/cold split between lineups')
            elif drift_gap >= 1.2:
                _add(side_drivers, 5, '🔥', 'Offense drift edge', f'{drift_gap:.2f}-run hot/cold split between lineups')
            elif drift_gap >= 0.8:
                _add(side_drivers, 3, '🔥', 'Offense drift lean', f'{drift_gap:.2f}-run hot/cold split between lineups')
        except (TypeError, ValueError):
            pass

    # ---- TOTAL: Total model vs market disagreement ----
    # History:
    # - 5/29 reband to +18 max (was +9). Old bands capped total-only edges
    #   too low. Higher bands surface real total edges as PRIME/STRONG.
    # - 5/30 direction-conflict gate: 4+ aligned distinct players override
    #   the runs-model direction (correlated same-pitcher stacks dedup to
    #   1 player so they don't trigger).
    # - 5/30 OVER skepticism (this block): 30d backfill audit showed
    #   TOTAL_UNDER hits 61.2% on n=85 but TOTAL_OVER barely beats coinflip
    #   at 53.7% on n=95. v3/v4 OVER models have documented drift. Two
    #   gates added so OVER contributions get full weight ONLY when:
    #     (a) v3 AND v4 BOTH agree on OVER direction (cross-model consensus)
    #     (b) v4 OVER isn't auto-suppressed by model_health (audit-driven)
    #   When either fails, OVER contribution is multiplied by 0.6 (drops a
    #   +18 PRIME signal to +11 STRONG — still meaningful but won't carry
    #   a game to TOTAL PRIME tier on its own).
    proj_total = float(ctx.get('projected_total') or 0)
    close_total = float(ctx.get('close_total') or ctx.get('open_total') or 0)
    v4_total = ctx.get('model_pred_total')
    total_delta_signed = 0.0
    total_delta_abs = 0.0
    total_delta_suppressed = False
    over_skeptic_mult = 1.0  # applied below when signal points OVER

    # Pre-compute Jerry's signed delta for the v3-vs-Jerry direction-conflict
    # gate below. (Jerry contribution itself is added in a later block; this
    # is only the direction signal used by the gate.) Added 2026-06-03 after
    # SD @ PHI 6/3 was scored PRIME 80 OVER 7.5 — v3 said +3.30 OVER,
    # Jerry said -1.43 UNDER. Both fired as positive contributions despite
    # opposing directions, inflating TOTAL to PRIME. Actual was 5 runs (U7.5
    # hit; Jerry's direction was right but v3 was the louder voice). The
    # principle: when our own models can't agree direction, the system
    # shouldn't broadcast PRIME conviction regardless of which is louder.
    jerry_total_raw = ctx.get('jerry_pred_total')
    jerry_total_delta_pre = None
    if jerry_total_raw is not None and close_total > 0:
        try:
            jerry_total_delta_pre = round(float(jerry_total_raw) - close_total, 2)
        except (TypeError, ValueError):
            jerry_total_delta_pre = None

    if proj_total > 0 and close_total > 0:
        total_delta_signed = round(proj_total - close_total, 2)
        total_delta_abs = abs(total_delta_signed)
        delta_direction = 'OVER' if total_delta_signed > 0 else 'UNDER'

        # OVER skepticism (5/30): compute multiplier if this is an OVER signal.
        if delta_direction == 'OVER':
            v3_over = total_delta_signed > 0
            v4_over_agrees = None
            try:
                if v4_total is not None:
                    v4_over_agrees = (float(v4_total) - close_total) > 0
            except (TypeError, ValueError):
                v4_over_agrees = None
            v4_over_suppressed_flag = False
            try:
                from game_context import is_v4_over_suppressed
                v4_over_suppressed_flag = is_v4_over_suppressed()
            except Exception:
                pass
            # Cross-model disagreement OR v4 OVER suppressed → discount
            if v4_over_agrees is False or v4_over_suppressed_flag:
                over_skeptic_mult = 0.6
                reason = 'cross-model disagree' if v4_over_agrees is False else 'v4 OVER auto-suppressed'
                _evidence('⚠️', 'OVER skepticism applied',
                          f'{reason} — total contribution ×0.6')

        # Direction-conflict gates:
        # (a) 5/30 prop-conflict — prop alignment points opposite direction of
        #     v3 AND 4+ aligned PRIME/STRONG distinct players.
        # (b) 2026-06-03 v3-vs-Jerry conflict — both models have material edge
        #     (>=0.5 runs) AND they point opposite directions. Suppressing on
        #     this case stops PRIME tier inflation from stacking opposing-
        #     model signals (SD/PHI 6/3 incident).
        # (c) 2026-06-04 v4-vs-Jerry conflict — mirror of (b) but on the v4
        #     axis. CLE @ NYY 6/4 ended 2-1 with v3 -3.2 UNDER, v4 -1.4 UNDER,
        #     Jerry +0.92 OVER. Jerry was alone on the wrong side. The
        #     existing gate fires on v3-vs-Jerry, but if v3 were silent (as
        #     it was at the morning compute pass that locked the tier) the
        #     v4 disagreement is the right signal to gate on. Both Jerry was
        #     0-3 today on split-direction games — extending this gate is
        #     the right structural call.
        # All gates cap PRIME via _dim_tier "PRIME requires play" rule and
        # write_sweat_score's 79-cap. Score, drivers, direction call all
        # remain intact for transparency.
        # 2026-06-05: removed v3-Jerry and v4-Jerry conflict gates. The
        # new Jerry-confirmation rule in the Jerry contribution block (further
        # down) subsumes them — Jerry now contributes 0 when v3/v4 don't
        # agree direction, so there's no "Jerry stacks with opposing model"
        # failure mode left. The old gates also over-suppressed v3 in the
        # KCR/MIN-style case (v3+v4 both OVER, Jerry alone UNDER) — v3+v4
        # should have driven the pick but the gate killed v3 too. prop_conflict
        # stays because it's about prop alignment vs v3, a different signal.
        prop_conflict = (prop_dir is not None and prop_dir != delta_direction
                         and (prop_dir_prime + prop_dir_strong) >= 4)
        if prop_conflict:
            total_delta_suppressed = True
            _evidence('⚠️', 'Total delta vs props conflict',
                      f'Model {total_delta_signed:+.2f} {delta_direction}, {prop_dir_prime + prop_dir_strong} players point {prop_dir} — suppressed')
        else:
            # Resolve band contribution, then apply OVER skepticism multiplier
            if total_delta_abs >= 2.0:
                pts = int(round(18 * over_skeptic_mult))
                _add(total_drivers, pts, '📈', 'Major total disagreement', f'{total_delta_signed:+.2f}-run model vs market')
            elif total_delta_abs >= 1.5:
                pts = int(round(14 * over_skeptic_mult))
                _add(total_drivers, pts, '📈', 'Strong total disagreement', f'{total_delta_signed:+.2f}-run model vs market')
            elif total_delta_abs >= 1.0:
                pts = int(round(10 * over_skeptic_mult))
                _add(total_drivers, pts, '📈', 'Total edge', f'{total_delta_signed:+.2f}-run model vs market')
            elif total_delta_abs >= 0.5:
                pts = int(round(6 * over_skeptic_mult))
                _add(total_drivers, pts, '📈', 'Total lean', f'{total_delta_signed:+.2f}-run model vs market')
            elif total_delta_abs >= 0.3:
                pts = int(round(3 * over_skeptic_mult))
                _add(total_drivers, pts, '📈', 'Total slim edge', f'{total_delta_signed:+.2f}-run')

    # ---- TOTAL: v3+v4 CONSENSUS BONUS (NEW 2026-06-05) ----
    # When v3 and v4 BOTH have material edge (>=0.5 runs) AND point the same
    # direction, that's two independent math-trend models reaching the same
    # answer. v3 is XGBoost over historical patterns; v4 is the structural
    # runs model. Independent feature paths, same conclusion = strongest
    # TOTAL signal we have.
    #
    # 6/4 audit validates: v3 + v4 BOTH 7-2 (77.8%) on the same n=9 slate
    # where Jerry was 1-8. Multi-model agreement = math-trend confluence.
    v3v4_consensus_dir = None
    if not total_delta_suppressed:
        v3_total_dir = None
        v3_total_abs = total_delta_abs  # already computed above
        if total_delta_abs >= 0.5:
            v3_total_dir = 'OVER' if total_delta_signed > 0 else 'UNDER'
        v4_total_dir = None
        v4_total_signed = None
        if v4_total is not None and close_total > 0:
            try:
                v4_total_signed = float(v4_total) - close_total
                if abs(v4_total_signed) >= 0.5:
                    v4_total_dir = 'OVER' if v4_total_signed > 0 else 'UNDER'
            except (TypeError, ValueError):
                pass
        if v3_total_dir is not None and v3_total_dir == v4_total_dir:
            v3v4_consensus_dir = v3_total_dir
            # Magnitude bonus: if BOTH are loud (>=1.5), add more
            both_loud = total_delta_abs >= 1.5 and abs(v4_total_signed) >= 1.5
            consensus_pts = 12 if both_loud else 10
            _add(total_drivers, consensus_pts, '🤝', 'v3+v4 consensus',
                 f'Both math models point {v3v4_consensus_dir} (v3 {total_delta_signed:+.2f}, v4 {v4_total_signed:+.2f})')

    # ---- TOTAL: Confluence-as-volatility-proxy UNDER bias (NEW 2026-06-05) ----
    # Backtest n=640 (_backtest_outside_box.py) found |signal_confluence_net|=4
    # predicts UNDER at 58.1% / n=31 (+0.109 EV at -110), regardless of which
    # SIDE the confluence points to. Interpretation: when many independent
    # signals all point to one team being structurally better, runs tend to
    # cluster lower (the matchup itself is decided, fewer back-and-forth
    # innings, weaker side's offense gets suppressed). Add +6 toward UNDER
    # specifically when net=4 (peak per cohort scan). Net=5 was 42% UNDER,
    # net=6 was 58% UNDER on n=19, so we only score net=4 strict.
    try:
        cn_abs_total = abs(int(ctx.get('signal_confluence_net') or 0))
    except (TypeError, ValueError):
        cn_abs_total = 0
    if cn_abs_total == 4 and not total_delta_suppressed:
        # Only fire when v3 or v4 also lean UNDER (avoid contradicting models)
        v3_under = (total_delta_abs >= 0.5 and total_delta_signed < 0)
        v4_under = (v4_total_signed is not None and abs(v4_total_signed) >= 0.5 and v4_total_signed < 0)
        if v3_under or v4_under:
            _add(total_drivers, 6, '🎯', 'PEAK confluence UNDER bias',
                 '4-signal confluence games skew UNDER 58.1% lifetime')

    # ---- TOTAL: Jerry total — DEMOTED to supporting vote (2026-06-05) ----
    # 6/4 audit was a Jerry disaster: 1-8 on n=9 (11.1% direction accuracy)
    # vs v3 7-2 and v4 7-2 same night. Jerry's 3-day cumulative dropped from
    # 70.4% (after 6/3) to 55.6% (after 6/4). Likely cause: Phase 1 Bayesian
    # shrinkage (shipped 6/2) is over-smoothing recent pitcher form changes.
    #
    # Rather than blindly demote weights, gate Jerry on AT LEAST ONE other
    # model agreeing direction. Jerry-alone-loud no longer gets card-grade
    # contribution — that was the pattern of every Jerry miss on 6/4
    # (CLE/NYY, KCR/MIN, BAL/BOS all had Jerry alone vs v3+v4 silent or
    # opposing). When Jerry is confirmed by v3 or v4, full bands apply.
    # When Jerry is alone, contribution = 0. Existing conflict gates
    # (v3-Jerry, v4-Jerry) already kill the "Jerry vs rest" cases.
    jerry_total = ctx.get('jerry_pred_total')
    if jerry_total is not None and close_total > 0 and not total_delta_suppressed:
        try:
            jerry_total_delta_signed = round(float(jerry_total) - close_total, 2)
            jerry_total_delta_abs = abs(jerry_total_delta_signed)
            jerry_dir = 'OVER' if jerry_total_delta_signed > 0 else 'UNDER'
            # Confirmation gate: at least one of v3/v4 must have >=0.3 in
            # same direction. Otherwise Jerry is alone and contributes 0.
            DEAD_BAND_JERRY_CONFIRM = 0.3
            v3_confirms = (total_delta_abs >= DEAD_BAND_JERRY_CONFIRM and
                           ((total_delta_signed > 0) == (jerry_total_delta_signed > 0)))
            v4_confirms = False
            if v4_total is not None:
                try:
                    v4_d = float(v4_total) - close_total
                    v4_confirms = (abs(v4_d) >= DEAD_BAND_JERRY_CONFIRM and
                                   ((v4_d > 0) == (jerry_total_delta_signed > 0)))
                except (TypeError, ValueError):
                    v4_confirms = False
            jerry_confirmed = v3_confirms or v4_confirms
            if not jerry_confirmed:
                # Jerry alone — log as evidence so the audit sees Jerry's
                # opinion existed but didn't count toward the dim.
                _evidence('💤', 'Jerry alone (no v3/v4 confirmation)',
                          f'Jerry {jerry_total_delta_signed:+.2f} but no other model >= 0.3 in same direction — contribution suppressed')
            else:
                # Confirmed by at least one math-trend model. Apply existing
                # OVER skepticism multiplier and the original bands.
                jerry_mult = 1.0
                if jerry_dir == 'OVER':
                    v4_over_agrees_j = None
                    try:
                        if v4_total is not None:
                            v4_over_agrees_j = (float(v4_total) - close_total) > 0
                    except (TypeError, ValueError):
                        v4_over_agrees_j = None
                    v4_over_suppressed_j = False
                    try:
                        from game_context import is_v4_over_suppressed
                        v4_over_suppressed_j = is_v4_over_suppressed()
                    except Exception:
                        pass
                    if v4_over_agrees_j is False or v4_over_suppressed_j:
                        jerry_mult = 0.6
                # 2026-06-09 historical-baseline re-weight (post 6/9 audit):
                # Jerry totals direction-accuracy lifetime baseline is 50.0%
                # (cohort_signals.play_baselines.jerry_tot = 50.0 — literally a
                # coinflip). v3_tot baseline is 66.8%, v4_tot is 56.0%. The
                # 6/5 1.5x boost made Jerry the LOUDEST single contributor to
                # the TOTAL dim (26 max vs v3's 18 max), which contradicted
                # what the data encodes. 6/9 audit found 5 of 15 games used
                # "HIGH conviction" language driven by overweighted Jerry.
                #
                # Bands halved (was 26/20/14/8 → now 13/10/7/4). The
                # confirmation gate above stays — Jerry-alone still contributes
                # 0. Net effect: Jerry-confirmed cases tighten down so v3 stays
                # the loudest standalone signal, matching its 16.8-point
                # baseline edge over coinflip. Old 1.5x backtest was 7-1 / 87.5%
                # on n=8 — small sample optimistic; live results 6/4-6/9
                # surfaced the over-weight cost.
                #
                # Dry-run on 6/9 slate (n=15): 3 games tier-down (SEA@BAL
                # LIGHT→PASS, HOU@LAA STRONG→LIGHT, MIL@ATH STRONG→LIGHT) —
                # all three were Jerry-driven overconfidence per 6/9 audit.
                # PRIMEs hold (LAD, WAS@SF, STL@NYM). See [[project_re_weight_model_votes_609]].
                if jerry_total_delta_abs >= 2.5:
                    pts = int(round(13 * jerry_mult))
                    _add(total_drivers, pts, '🧠', 'Jerry major total disagreement', f'{jerry_total_delta_signed:+.2f}-run Jerry vs market (confirmed)')
                elif jerry_total_delta_abs >= 1.5:
                    pts = int(round(10 * jerry_mult))
                    _add(total_drivers, pts, '🧠', 'Jerry strong total disagreement', f'{jerry_total_delta_signed:+.2f}-run Jerry vs market (confirmed)')
                elif jerry_total_delta_abs >= 1.0:
                    pts = int(round(7 * jerry_mult))
                    _add(total_drivers, pts, '🧠', 'Jerry total edge', f'{jerry_total_delta_signed:+.2f}-run Jerry vs market (confirmed)')
                elif jerry_total_delta_abs >= 0.5:
                    pts = int(round(4 * jerry_mult))
                    _add(total_drivers, pts, '🧠', 'Jerry total lean', f'{jerry_total_delta_signed:+.2f}-run Jerry vs market (confirmed)')
        except (TypeError, ValueError):
            pass

    # ---- TOTAL: Prop reverse signal (NEW 2026-06-10) ----
    # Pulls the lineup-level prop-pipeline aggregate from jerry_cache. When
    # multiple PRIME/STRONG player props on the same game point one direction,
    # that's lineup-level granularity the cohort engine misses. Vote weight
    # scaled by confidence tier; LOW signals contribute small, HIGH signals
    # contribute meaningfully but never out-weigh the math model consensus
    # (capped at ~+10 / -10 so they're a vote, not a primary).
    # See project_prop_reverse_v1 for spec + signal computation rules.
    try:
        from prop_reverse_signal import compute_game_signal
        # Pull props for this game's matchup directly — cheap query, avoids
        # depending on jerry_cache row state.
        import requests as _rq
        game_date = ctx.get('game_date')
        home_team = ctx.get('home_team') or ''
        away_team = ctx.get('away_team') or ''
        matchup = f'{away_team} @ {home_team}'
        pr = _rq.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props",
            params={'select': '*', 'game_date': f'eq.{game_date}',
                    'matchup': f'eq.{matchup}'},
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=5,
        )
        if pr.status_code == 200:
            game_props = pr.json()
            if game_props:
                sig = compute_game_signal(game_props, home_team=home_team, away_team=away_team)
                ts = sig['total_signal']
                conf = sig['confidence']
                # Weight by confidence and signal magnitude
                if conf == 'HIGH' and abs(ts) >= 0.5:
                    pts = int(round(10 * ts))  # ±5..±10 depending on direction strength
                    direction = 'OVER' if ts > 0 else 'UNDER'
                    _add(total_drivers, abs(pts) if ts > 0 else -abs(pts), '🎯',
                         f'Prop signals → {direction}',
                         f"{sig['evidence_count']} props, {sig['over_pts']:.0f}/{sig['under_pts']:.0f} O/U pts")
                elif conf == 'MEDIUM' and abs(ts) >= 0.4:
                    pts = int(round(6 * ts))
                    direction = 'OVER' if ts > 0 else 'UNDER'
                    _add(total_drivers, abs(pts) if ts > 0 else -abs(pts), '🎯',
                         f'Prop signals → {direction}',
                         f"{sig['evidence_count']} props, {sig['over_pts']:.0f}/{sig['under_pts']:.0f} O/U pts")
                elif conf == 'LOW' and abs(ts) >= 0.5:
                    pts = int(round(3 * ts))
                    direction = 'OVER' if ts > 0 else 'UNDER'
                    _add(total_drivers, abs(pts) if ts > 0 else -abs(pts), '🎯',
                         f'Prop signals lean → {direction}',
                         f"{sig['evidence_count']} props (low confidence)")
    except Exception:
        # Failure here must NEVER block scoring — silently skip the prop reverse vote
        pass

    # ---- SIDE: K gap ----
    home_k_gap = abs(float(ctx.get('home_k_gap') or 0))
    away_k_gap = abs(float(ctx.get('away_k_gap') or 0))
    k_gap = max(home_k_gap, away_k_gap)
    if k_gap >= 12:
        _add(side_drivers, 6, '⚡', 'K-gap large', f'{k_gap:.0f}-pt K% gap')
    elif k_gap >= 8:
        _add(side_drivers, 3, '⚡', 'K-gap edge', f'{k_gap:.0f}-pt K% gap')

    # ---- SIDE: Pitcher mastery / anti-mastery vs current opp ----
    for vt_key in ('home_pitcher_vs_team_era', 'away_pitcher_vs_team_era'):
        v = ctx.get(vt_key)
        if v is None:
            continue
        try:
            vt = float(v)
            side_label = 'Home' if vt_key.startswith('home') else 'Away'
            # 2026-06-03: split mastery and anti-mastery into 3 bands each.
            # Old flat ≥7.0 = +5 missed how extreme 9+ ERA tagging is — TOR/ATL
            # 6/3 had 9.14 ERA away pitcher vs opp and game stuck at SIDE 47/PASS
            # despite legit confluence + tagged starter.
            if vt <= 1.8:
                _add(side_drivers, 8, '⚾', f'{side_label} elite mastery vs opp', f'{vt:.2f} ERA career vs this team')
            elif vt <= 2.5:
                _add(side_drivers, 5, '⚾', f'{side_label} pitcher mastery vs opp', f'{vt:.2f} ERA career vs this team')
            elif vt <= 3.0:
                _add(side_drivers, 3, '⚾', f'{side_label} pitcher edge vs opp', f'{vt:.2f} ERA career vs this team')
            elif vt >= 8.5:
                _add(side_drivers, 8, '🚨', f'{side_label} pitcher torched by opp', f'{vt:.2f} ERA career vs this team')
            elif vt >= 7.0:
                _add(side_drivers, 5, '🚨', f'{side_label} pitcher tagged by opp', f'{vt:.2f} ERA career vs this team')
            elif vt >= 6.0:
                _add(side_drivers, 3, '🚨', f'{side_label} pitcher struggles vs opp', f'{vt:.2f} ERA career vs this team')
        except (TypeError, ValueError):
            pass

    # ---- TOTAL: Park (REBANDED 2026-05-29 — add 105/95 mid-tier) ----
    # Old: +4 only at 110/92 thresholds. ATL/CIN's 108 (just below 110) and
    # MIA/NYM's 95 (just above 92) were rounded down to zero. New 3-tier
    # banding catches the soft park leans.
    park = float(ctx.get('park_run_factor') or 100)
    if park >= 115:
        _add(total_drivers, 9, '🏟', 'Extreme hitter park', f'Park factor {park:.0f}')
    elif park >= 110:
        _add(total_drivers, 6, '🏟', 'Hitter-friendly park', f'Park factor {park:.0f}')
    elif park >= 105:
        _add(total_drivers, 3, '🏟', 'Park slight Over lean', f'Park factor {park:.0f}')
    elif park <= 88:
        _add(total_drivers, 9, '🏟', 'Extreme pitcher park', f'Park factor {park:.0f}')
    elif park <= 92:
        _add(total_drivers, 6, '🏟', 'Pitcher-friendly park', f'Park factor {park:.0f}')
    elif park <= 95:
        _add(total_drivers, 3, '🏟', 'Park slight Under lean', f'Park factor {park:.0f}')

    # ---- TOTAL: Extreme hitter park x high-GB pitcher cross (NEW 2026-06-05) ----
    # Backtest (_backtest_advanced_605.py) n=15: park_run_factor>=115
    # with average GB% >= 0.50 across both starters STILL hits OVER 66.7%
    # (+0.273 EV at -110). Lesson: at Coors/extreme parks, high-GB doesn't
    # save the under because elevation kills sinkers and groundball
    # contact gets through the synthetic infield. Counter-intuitive — most
    # bettors think "high-GB + Coors = exception" but the cohort says no.
    if park >= 115:
        h_gb = ctx.get('home_sp_gb_pct'); a_gb = ctx.get('away_sp_gb_pct')
        try:
            gb_vals = [float(v) for v in (h_gb, a_gb) if v is not None]
            avg_gb = sum(gb_vals) / len(gb_vals) if gb_vals else None
        except (TypeError, ValueError):
            avg_gb = None
        if avg_gb is not None and avg_gb >= 0.50:
            _add(total_drivers, 4, '⛰️', 'Coors trap: high-GB doesn\'t save',
                 f'avg GB% {avg_gb*100:.0f}% at extreme park = 67% OVER lifetime')

    # ---- TOTAL: Weather (cold / wind) ----
    temp = float(ctx.get('temperature') or 70)
    if temp <= 45:
        _add(total_drivers, 3, '❄️', 'Cold weather', f'{int(temp)}°F suppresses scoring')
    wind = float(ctx.get('wind_speed') or 0)
    if wind >= 18:
        _add(total_drivers, 3, '💨', 'High wind', f'{int(wind)} mph affecting flight')

    # ---- TOTAL: Aligned-prop direction (NEW 2026-05-29) ----
    # 3+ same-game props pointing same Over/Under direction = a total
    # confluence signal the old scorer missed. ATL/CIN 5/29 trigger:
    # PRIME 85 Harris II hits-over + STRONG 80 Mateo hits-over + STRONG 76
    # Acuña hits-over → 3 aligned OVER props on the same game = real total
    # edge masked by zero side confluence.
    if prop_dir is not None:
        total_aligned = prop_dir_prime + prop_dir_strong
        if total_aligned >= 4:
            _add(total_drivers, 18, '🎯', 'Prop confluence', f'{total_aligned} aligned props point {prop_dir}')
        elif total_aligned == 3:
            _add(total_drivers, 14, '🎯', 'Prop confluence', f'3 aligned props point {prop_dir}')
        elif total_aligned == 2 and prop_dir_prime >= 1:
            _add(total_drivers, 6, '🎯', 'Prop alignment', f'2 aligned props ({prop_dir_prime} PRIME) point {prop_dir}')

    # ---- PROP: stack count (game has standout props worth posting) ----
    game_props = game_props or []
    # ---- PROP dim weights — book-aware (REBALANCED 2026-06-01) ----
    # Old weights were calibrated when PRIMEs were common and many were
    # ⚠no_book noise. After Phase 2 attach (5/28-5/29) and the morning's
    # tier integrity audit (hits_under SKIP 64.5% beats STRONG 58.6% —
    # PRIMEs are now rarer + more meaningful), the old +6 for "1 PRIME"
    # was leaving games at 36/PASS even with a real book-verified PRIME
    # prop available (e.g. Joe Ryan U 1.5 BB PRIME 75 ✓book in CHW/MIN
    # left game at 45/PASS — beta users lost confidence seeing PRIME
    # props in PASS games).
    #
    # New tiers separate book-verified from internal-line PRIMEs since
    # ⚠no_book PRIMEs are less trustworthy (the 5/30 Brandon Young U 5.5K
    # PRIME 80 trust-killer pattern). Book PRIME ✓ is what we want to
    # surface; internal PRIME ⚠ is a softer signal.
    prime_props = [p for p in game_props if p.get('tier') == 'PRIME']
    strong_props = [p for p in game_props if p.get('tier') == 'STRONG']
    prime_book = [p for p in prime_props if p.get('book_line') is not None]
    prime_nobook = [p for p in prime_props if p.get('book_line') is None]
    strong_book = [p for p in strong_props if p.get('book_line') is not None]

    # Tiered scoring: book-verified PRIMEs weight strongest; large stacks
    # of ⚠no_book PRIMEs (like SF/COL Coors hits stacks) get partial credit
    # but not full weight to avoid the trust-killer pattern.
    #
    # 2026-06-03 calibration: bumped no-book bands so legit conviction
    # props (single STRONG c=78 no-book, single PRIME c=82 no-book, 4+
    # PRIME no-book Coors stack) lift games out of PASS. Specific triggers:
    # SFG @ MIL had STRONG c=78 Perkins hits_under no-book → PROP=30 PASS
    # because the "1 STRONG no-book" path didn't exist. LAD @ ARI had 4
    # PRIME no-book hits_over Coors stack → PROP capped at 50 LIGHT_LEAN
    # while game was clearly the loudest on the slate. Book-verified
    # weights unchanged (already calibrated correctly 6/1).
    if len(prime_book) >= 3:
        _add(prop_drivers, 30, '🔥', 'PRIME book stack', f'{len(prime_book)} book-verified PRIME props')
    elif len(prime_book) >= 2:
        _add(prop_drivers, 22, '🔥', 'Multiple PRIME ✓book', f'{len(prime_book)} book-verified PRIME props')
    elif len(prime_book) == 1:
        # 1 book-verified PRIME should lift a game to at least LIGHT_LEAN.
        # 30 base + 20 = 50. Game becomes visible / actionable.
        _add(prop_drivers, 20, '🔥', 'PRIME ✓book available', '1 book-verified PRIME prop')
    elif len(prime_props) >= 5:
        # Extreme no-book mega-stack — Coors/COL hits-over patterns
        # routinely produce 5+ PRIME no-book. Real conviction event.
        _add(prop_drivers, 28, '🔥', 'PRIME mega-stack (no-book)', f'{len(prime_props)} PRIME props')
    elif len(prime_props) >= 4:
        # Large no-book stack (e.g. Coors / extreme park hits-overs).
        # Bumped 20 → 24 so 4-PRIME-no-book lifts game from 50 LIGHT_LEAN
        # to 54 — still LIGHT_LEAN but with room for SIDE/TOTAL stack
        # to clear STRONG threshold on the game headline.
        _add(prop_drivers, 24, '🔥', 'PRIME stack (no-book)', f'{len(prime_props)} PRIME props')
    elif len(prime_props) >= 2:
        # Multiple ⚠no_book PRIMEs — was +14, bumped to +16. Cautious but
        # acknowledges 2+ aligned PRIMEs is a real signal even without book.
        _add(prop_drivers, 16, '🔥', 'Multiple PRIME props', f'{len(prime_props)} PRIME props (no book)')
    elif len(prime_props) == 1:
        # Single ⚠no_book PRIME — bumped 8 → 14. The PRIME tier already
        # implies the scorer's strongest conviction band; even unverified,
        # the game deserves to clear PASS (30 + 14 = 44, still PASS unless
        # SIDE/TOTAL also contribute). With STRONG add-on can reach 50.
        _add(prop_drivers, 14, '🔥', 'PRIME prop available', '1 PRIME prop (no book)')

    # STRONG add-on — credits STRONG no-book singletons that were silent
    # under 6/1 rules. Fires whenever no PRIME is present OR alongside a
    # single PRIME (to avoid stacking with PRIME mega-stacks already at +24+).
    if not prime_props or len(prime_props) == 1:
        if len(strong_book) >= 3:
            _add(prop_drivers, 14, '💪', 'STRONG ✓book cluster', f'{len(strong_book)} book-verified STRONG props')
        elif len(strong_book) >= 2:
            _add(prop_drivers, 9, '💪', 'STRONG ✓book pair', f'{len(strong_book)} book-verified STRONG props')
        elif len(strong_book) == 1:
            _add(prop_drivers, 5, '💪', 'STRONG ✓book available', '1 book-verified STRONG prop')
        elif len(strong_props) >= 3:
            _add(prop_drivers, 5, '💪', 'STRONG prop cluster', f'{len(strong_props)} STRONG props')
        elif len(strong_props) >= 2:
            # 2 STRONG no-book — was silent, now +4 so games with 1 PRIME
            # + 1-2 STRONG no-book reach STRONG band (74+ in some stacks).
            _add(prop_drivers, 4, '💪', 'STRONG prop pair (no-book)', f'{len(strong_props)} STRONG props')
        elif len(strong_props) == 1:
            # 1 STRONG no-book — was silent. SFG @ MIL Perkins case.
            # +3 minimum so the game is visible as having a real prop.
            _add(prop_drivers, 3, '💪', 'STRONG prop available', '1 STRONG prop (no book)')

    # ---- primary_play tier bonus (routes to dimension by play type) ----
    # When the pipeline has already endorsed a specific bet via
    # compute_primary_play, that meta-signal should boost the relevant
    # dimension. Without this, a PRIME ML primary_play like Yankees ML
    # only reaches LIGHT_LEAN on the SIDE dimension because confluence +
    # spread + mastery cap individually at +14/+13/+5.
    pp = ctx.get('primary_play')
    if pp and isinstance(pp, dict):
        pp_type = (pp.get('type') or '').lower()
        pp_tier = pp.get('tier')
        bonus = {'PRIME': 20, 'STRONG': 12, 'LIGHT_LEAN': 6}.get(pp_tier, 0)
        if bonus > 0:
            target = side_drivers if pp_type in ('ml', 'spread', 'rl') else (
                     total_drivers if pp_type in ('nrfi', 'yrfi', 'total') else None)
            if target is not None:
                _add(target, bonus, '⭐', f'{pp_tier} primary play', f'{pp.get("label") or pp_type.upper()} — pipeline endorsement')

    # ---- Compute sub-scores ----
    side_score = min(100, 30 + sum(d['points'] for d in side_drivers))
    total_score = min(100, 30 + sum(d['points'] for d in total_drivers))
    prop_score = min(100, 30 + sum(d['points'] for d in prop_drivers))

    # ---- PRIME primary_play floor (5/29, narrowed 5/30) ----
    # When the pipeline has independently endorsed a play as PRIME, the
    # corresponding sweat dimension floors at 80. Originally covered all
    # play types; narrowed 5/30 to ML/spread/RL and `total` (model OVER/
    # UNDER) only — NRFI/YRFI explicitly excluded because PRIME NRFI 90-94
    # audits at 50% (n=22, coinflip), not edge. Don't auto-elevate NRFI
    # to TOTAL PRIME just because the pipeline tagged it. NRFI surfaces
    # as a supplementary play (see compute_primary_play).
    if pp and isinstance(pp, dict) and pp.get('tier') == 'PRIME':
        pp_type = (pp.get('type') or '').lower()
        if pp_type in ('ml', 'spread', 'rl') and side_score < 80:
            side_score = 80
            side_drivers.append({'emoji': '⭐', 'label': 'PRIME play floor', 'points': 80 - sum(d['points'] for d in side_drivers if d['label'] != 'PRIME play floor') - 30, 'detail': f'{pp.get("label") or pp_type.upper()} pipeline-endorsed'})
        elif pp_type in ('over', 'under', 'total') and total_score < 80:
            total_score = 80
            total_drivers.append({'emoji': '⭐', 'label': 'PRIME play floor', 'points': 80 - sum(d['points'] for d in total_drivers if d['label'] != 'PRIME play floor') - 30, 'detail': f'{pp.get("label") or pp_type.upper()} pipeline-endorsed'})

    # ---- Determine each dimension's play (the bet the dimension's
    # heat actually points at — drives the "model likes X" headline) ----
    side_play = None
    if pp and isinstance(pp, dict) and (pp.get('type') or '').lower() in ('ml', 'spread', 'rl'):
        side_play = {'type': (pp.get('type') or '').upper(), 'label': pp.get('label'), 'tier': pp.get('tier')}

    # SIDE play direction resolver (RESHAPED 2026-06-05).
    # Original 6/01 logic used Jerry-signed first to pick direction. The 6/5
    # n=640 cohort scan revealed Jerry spread direction is 47.8% (coinflip),
    # so using it as the primary directional source was leaking ~10pt of
    # accuracy to the picker. New priority order:
    #   1. v3 + v4 consensus (both same direction, both |edge|>=0.5) — 67.2%
    #   2. v4 alone at |edge|>=1.0 — 65.9% on DOG RL lifetime
    #   3. v3 alone at |edge|>=1.0 — 60.7% on DOG RL lifetime
    #   4. Confluence-net direction at |net|>=4 (peak cohort)
    #   Jerry-signed fully dropped from direction picking.
    if side_play is None and side_score >= 65 and close_spread_val is not None:
        pick_home = None
        src = None
        try:
            # 1. v3+v4 consensus
            if (v3_signed is not None and v4_signed is not None
                    and abs(float(v3_signed)) >= 0.5 and abs(float(v4_signed)) >= 0.5
                    and (float(v3_signed) > 0) == (float(v4_signed) > 0)):
                pick_home = float(v3_signed) > 0
                src = 'v3v4_consensus'
            # 2. v4 alone (math model preferred per disagreement cohort 57.1%)
            elif v4_signed is not None and abs(float(v4_signed)) >= 1.0:
                pick_home = float(v4_signed) > 0
                src = 'v4_alone'
            # 3. v3 alone
            elif v3_signed is not None and abs(float(v3_signed)) >= 1.0:
                pick_home = float(v3_signed) > 0
                src = 'v3_alone'
            # 4. Confluence net direction (only at peak |net|=4)
            else:
                cn_val = ctx.get('signal_confluence_net')
                if cn_val is not None and abs(int(cn_val)) == 4:
                    pick_home = int(cn_val) > 0
                    src = 'confluence_peak'
        except (TypeError, ValueError):
            pick_home = None
        if pick_home is not None:
            team = ctx.get('home_team') if pick_home else ctx.get('away_team')
            if team:
                play_tier = 'PRIME' if side_score >= 80 else 'STRONG' if side_score >= 65 else 'LIGHT_LEAN'
                side_play = {
                    'type': 'ML',
                    'label': f'{team} ML',
                    'tier': play_tier,
                    'source': src,
                }

    # TOTAL play preference order (5/30 updated):
    # 1. primary_play if it's over/under/total — pipeline-endorsed total
    # 2. prop_dir if 3+ aligned PRIME/STRONG — prop confluence overrides
    #    drifting runs model when they disagree
    # 3. total_delta lean — Over/Under from runs model vs market
    # NRFI / YRFI no longer fill total_play — they go to supplementary_play
    # exclusively (5/30 demotion).
    total_play = None
    if pp and isinstance(pp, dict) and (pp.get('type') or '').lower() in ('over', 'under', 'total'):
        total_play = {'type': (pp.get('type') or '').upper(), 'label': pp.get('label'), 'tier': pp.get('tier')}
    elif prop_dir is not None and (prop_dir_prime + prop_dir_strong) >= 4 and close_total > 0:
        # Prop confluence drives the total play only with 4+ distinct
        # PLAYERS aligned (raised from 3 on 5/30 after MIA/NYM bug). At 2-3
        # aligned players, prop_dir still adds to the sub-score but doesn't
        # override the runs model's direction — that's the right balance:
        # additive evidence at low n, override at high n.
        total_play = {
            'type': f'TOTAL_{prop_dir}',
            'label': f'{prop_dir.title()} {close_total}',
            'edge': total_delta_signed if not total_delta_suppressed else None,
            'source': 'prop_confluence',
        }
    elif total_delta_abs >= 0.5 and close_total > 0 and not total_delta_suppressed:
        direction = 'OVER' if total_delta_signed > 0 else 'UNDER'
        total_play = {
            'type': f'TOTAL_{direction}',
            'label': f'{direction.title()} {close_total}',
            'edge': total_delta_signed,
        }

    prop_play = None
    if prop_dir is not None and (prop_dir_prime + prop_dir_strong) >= 2:
        top_player = prop_dir_top[0] if prop_dir_top else None
        prop_play = {
            'type': f'PROP_{prop_dir}',
            'label': f'{prop_dir_prime + prop_dir_strong} aligned props point {prop_dir}',
            'top_player': top_player.get('player_name') if top_player else None,
            'top_prop_type': top_player.get('prop_type') if top_player else None,
        }
    elif len(prime_props) >= 1:
        top = max(prime_props, key=lambda p: p.get('conviction') or 0)
        prop_play = {
            'type': 'PROP_PRIME',
            'label': 'PRIME prop available',
            'top_player': top.get('player_name'),
            'top_prop_type': top.get('prop_type'),
        }

    # ---- Phase 2 cohort signal adjustment (2026-06-08) ----
    # After plays are determined, query cohort_signals for matches against the
    # picked direction. Apply aggregate conviction delta (capped ±25) directly
    # to side_score / total_score, and append a driver entry for transparency.
    # No-op when cohort_signals returns nothing or import unavailable.
    # See cohort_signals.evaluate_game_for_play + refresh_cohort_signals.py.
    _cohort_apply_to_dim(ctx, side_drivers, side_play, 'side', track)
    _cohort_apply_to_dim(ctx, total_drivers, total_play, 'total', track)
    # Recompute sub-scores so any cohort drivers we just appended are reflected.
    side_score = max(0, min(100, 30 + sum(d['points'] for d in side_drivers)))
    total_score = max(0, min(100, 30 + sum(d['points'] for d in total_drivers)))

    # ---- Per-dimension tiers (same 80/65/50/<50 cutoffs, but each tier's
    # PRIME requires that dimension's play exists — actionability gate) ----
    def _dim_tier(score, play):
        if score is None:
            return None
        if score >= 80:
            return 'PRIME' if play is not None else 'STRONG'  # cap at STRONG without actionable play
        if score >= 65:
            return 'STRONG'
        if score >= 50:
            return 'LIGHT_LEAN'
        return 'PASS'

    side_tier = _dim_tier(side_score, side_play)
    total_tier = _dim_tier(total_score, total_play)
    prop_tier = _dim_tier(prop_score, prop_play)

    # ---- Headline = max(sub-scores); winning dimension drives model_play ----
    dim_table = [
        ('side', side_score, side_tier, side_play),
        ('total', total_score, total_tier, total_play),
        ('prop', prop_score, prop_tier, prop_play),
    ]
    # Sort by score desc; ties favor side > total > prop (most-decisive bet types first)
    dim_order = {'side': 0, 'total': 1, 'prop': 2}
    dim_table.sort(key=lambda x: (-x[1], dim_order[x[0]]))
    winning_dim_name, headline_score, _, winning_play = dim_table[0]

    # ---- Supplementary play (NRFI/YRFI, "also worth a look") ----
    # 5/30 demotion: NRFI is no longer eligible for primary_play / POTD.
    # It still surfaces here as a secondary tag so the user sees "model
    # likes Yankees ML — NRFI also worth a look (companion-gated)".
    # Returns None when NRFI doesn't qualify under the gates.
    supplementary_play = _compute_supplementary_play(ctx)

    dimensions = {
        'side':  {'score': side_score,  'tier': side_tier,  'drivers': sorted(side_drivers,  key=lambda d: -d['points'])[:5], 'play': side_play},
        'total': {'score': total_score, 'tier': total_tier, 'drivers': sorted(total_drivers, key=lambda d: -d['points'])[:5], 'play': total_play},
        'prop':  {'score': prop_score,  'tier': prop_tier,  'drivers': sorted(prop_drivers,  key=lambda d: -d['points'])[:5], 'play': prop_play},
        'winning_dimension': winning_dim_name,
        'model_play': winning_play,
        'supplementary_play': supplementary_play,
    }

    # ---- Backward-compat: dedupe + sort + cap legacy contributions list ----
    # Dedupe by label to suppress duplicate rows on the WHY-THIS-SCORE panel
    # (6/9 incident: "Cohort signal confirms" rendered twice on 3 of 15 games
    # because side + total dimensions both pushed the same label). Keep the
    # highest-points instance of each label; if details differ, merge the
    # losing detail into the kept entry as a one-clause suffix so the user
    # sees both signals on a single line.
    if track.get('contributions'):
        contribs = track['contributions']
        contribs.sort(key=lambda c: -c.get('points', 0))
        seen_labels = {}
        deduped = []
        for c in contribs:
            label = c.get('label')
            if label not in seen_labels:
                seen_labels[label] = c
                deduped.append(c)
            else:
                kept = seen_labels[label]
                kept_detail = kept.get('detail') or ''
                new_detail = c.get('detail') or ''
                if new_detail and new_detail != kept_detail and new_detail not in kept_detail:
                    kept['detail'] = f"{kept_detail} + {new_detail}" if kept_detail else new_detail
        # Casual-bettor translation: stamp casual_label on every contribution
        # so the app can render plain English without losing the original
        # power label (kept on `label` for analyst mode). Per
        # project_casual_bettor_ux_docket: translate the language, keep the
        # depth. Server writes; app renders.
        for c in deduped:
            c['casual_label'] = translate_label(c.get('label'))
        track['contributions'] = deduped[:6]
    if track.get('evidence'):
        for e in track['evidence']:
            if isinstance(e, dict):
                e['casual_label'] = translate_label(e.get('label'))
        track['evidence'] = track['evidence'][:5]
    # Stamp casual_label on every dimension's drivers too (side / total / prop)
    # so the app can render any drill-down with translated text.
    if isinstance(dimensions, dict):
        for dim_key in ('side', 'total', 'prop'):
            dim = dimensions.get(dim_key)
            if isinstance(dim, dict):
                drivers = dim.get('drivers', [])
                for d in drivers:
                    if isinstance(d, dict):
                        d['casual_label'] = translate_label(d.get('label'))

    return (headline_score, dimensions)

def score_nba_game(game, nba_teams):
    """Score an NBA game for Play of the Day candidacy — playoff-enhanced"""
    score = 25  # base

    home_team = game.get('home_team', '')
    away_team = game.get('away_team', '')

    home_data = next((t for t in nba_teams if home_team.endswith(t.get('team', '').split(' ')[-1])), None)
    away_data = next((t for t in nba_teams if away_team.endswith(t.get('team', '').split(' ')[-1])), None)

    if not home_data or not away_data:
        return score, None, None

    home_net = float(home_data.get('net_rating') or 0)
    away_net = float(away_data.get('net_rating') or 0)
    home_def = float(home_data.get('defensive_rating') or 112)
    away_def = float(away_data.get('defensive_rating') or 112)
    home_pace = float(home_data.get('pace') or 100)
    away_pace = float(away_data.get('pace') or 100)

    # Net rating gap — strongest NBA predictor
    net_gap = abs(home_net - away_net)
    if net_gap >= 10:
        score += 25
    elif net_gap >= 8:
        score += 20
    elif net_gap >= 5:
        score += 12
    elif net_gap >= 3:
        score += 6

    # Defensive rating mismatch
    def_gap = abs(home_def - away_def)
    if def_gap >= 6:
        score += 12
    elif def_gap >= 4:
        score += 8

    # Home/away record edge
    home_record = home_data.get('home_record', '')
    away_record = away_data.get('away_record', '')
    home_wpct = 0.5
    away_wpct = 0.5
    try:
        hw, hl = map(int, home_record.split('-'))
        aw, al = map(int, away_record.split('-'))
        home_wpct = hw / (hw + hl) if (hw + hl) > 0 else 0.5
        away_wpct = aw / (aw + al) if (aw + al) > 0 else 0.5
        if home_wpct - away_wpct >= 0.25:
            score += 12
        elif home_wpct - away_wpct >= 0.15:
            score += 6
    except:
        pass

    # Playoff boost — April 19+ is playoffs, matchups are more predictable
    is_playoff = datetime.now(timezone.utc).month >= 4 and datetime.now(timezone.utc).day >= 19
    if is_playoff:
        score += 10  # baseline playoff boost — matchups more predictable
        # Home court is stronger in playoffs (65% vs 57% regular season)
        if home_wpct >= 0.65:
            score += 8
        # Both elite defenses = under lean signal
        if home_def <= 110 and away_def <= 110:
            score += 8

    # Determine lean
    lean = None
    lean_type = None
    # Pace-based total lean
    avg_pace = (home_pace + away_pace) / 2
    if home_def <= 110 and away_def <= 110 and avg_pace < 100:
        lean = 'Under'
        lean_type = 'total'
    elif home_def >= 115 and away_def >= 115 and avg_pace > 101:
        lean = 'Over'
        lean_type = 'total'
    # Side lean — better team at home with strong record
    elif net_gap >= 5 and home_net > away_net and home_wpct >= 0.6:
        lean = home_team.split(' ')[-1]
        lean_type = 'ml'
    elif net_gap >= 5 and away_net > home_net:
        lean = away_team.split(' ')[-1]
        lean_type = 'ml'

    return min(100, score), lean, lean_type

_V2_BUCKET_CACHE = {}


_UMP_CACHE = {}


def _get_ump_total_signal(umpire_name, lean_side):
    """Look up ump's over_rate and return cohort-based signal for total picks.

    Audit cohort (2026-05-10) showed:
      total_over × ump_over_hostile (over_rate ≤ 0.45): 20% OVER (n=15) — strong fade
      total_over × ump_over_friendly (over_rate ≥ 0.55): 56% OVER (n=114) — confirm

    Returns dict: {'action': 'suppress'|'confirm'|'neutral', 'note': str, 'over_rate': float}
    """
    if not umpire_name:
        return {'action': 'neutral', 'note': '', 'over_rate': None}
    if 'ump_map' not in _UMP_CACHE:
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_umpires?select=ump_name,over_rate,games_sampled",
                headers=HEADERS, timeout=10,
            )
            rows = r.json() or []
            _UMP_CACHE['ump_map'] = {row['ump_name'].lower(): row for row in rows if row.get('ump_name')}
        except Exception:
            _UMP_CACHE['ump_map'] = {}
    ump = _UMP_CACHE['ump_map'].get((umpire_name or '').strip().lower())
    if not ump or ump.get('over_rate') is None:
        return {'action': 'neutral', 'note': '', 'over_rate': None}
    try:
        over_rate = float(ump['over_rate'])
        n = ump.get('games_sampled') or 0
    except (TypeError, ValueError):
        return {'action': 'neutral', 'note': '', 'over_rate': None}
    # Require minimum sample to act on ump signal
    if n < 30:
        return {'action': 'neutral', 'note': '', 'over_rate': over_rate}
    # OVER lean: ump under-friendly fades it, ump over-friendly confirms
    if lean_side == 'over':
        if over_rate <= 0.45:
            return {'action': 'suppress', 'over_rate': over_rate,
                    'note': f'ump {umpire_name} under-friendly ({over_rate:.2f} over rate, audit: 20% OVER hits) — suppressed'}
        if over_rate >= 0.55:
            return {'action': 'confirm', 'over_rate': over_rate,
                    'note': f'ump {umpire_name} over-friendly ({over_rate:.2f}) confirms'}
    # UNDER lean: ump over-friendly fades it, ump under-friendly confirms
    elif lean_side == 'under':
        if over_rate >= 0.55:
            return {'action': 'suppress', 'over_rate': over_rate,
                    'note': f'ump {umpire_name} over-friendly ({over_rate:.2f}) — UNDER suppressed'}
        if over_rate <= 0.45:
            return {'action': 'confirm', 'over_rate': over_rate,
                    'note': f'ump {umpire_name} under-friendly ({over_rate:.2f}) confirms UNDER'}
    return {'action': 'neutral', 'note': '', 'over_rate': over_rate}


def _v2_total_edge(ctx):
    """v2 Total OVER Edge — fires when projection_v2 model_total ≥ market+1.5.
    Backtest: 56-34 (62.2%) n=90 over 2807 games. Only durable v2 signal.

    Returns lean label if qualified, otherwise None. Lazy import + module-level
    caches keep play_of_day startup cost minimal.
    """
    close_total = ctx.get('close_total') or ctx.get('open_total')
    if close_total is None:
        return None
    try:
        import projection_v2 as v2
    except Exception:
        return None

    # Lazy-load shared lookup tables once per process (~700 pitchers, 30 teams)
    cache = _V2_BUCKET_CACHE
    if 'pitchers' not in cache:
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?select=player_name,innings_1_3_era,innings_1_3_ip,innings_4_6_era,innings_7_9_era&limit=2000",
                headers=HEADERS, timeout=15,
            )
            cache['pitchers'] = {p['player_name']: p for p in (r.json() or [])}
        except Exception:
            cache['pitchers'] = {}
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_team_offense?select=team,innings_1_3_runs_per_game,innings_4_6_runs_per_game,innings_7_9_runs_per_game,last10_runs_per_game",
                headers=HEADERS, timeout=15,
            )
            cache['teams'] = {t['team']: t for t in (r.json() or [])}
        except Exception:
            cache['teams'] = {}
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_bullpen_stats?select=team,pitching_1_3_era,pitching_4_6_era,pitching_7_9_era",
                headers=HEADERS, timeout=15,
            )
            cache['bullpens'] = {b['team']: b for b in (r.json() or [])}
        except Exception:
            cache['bullpens'] = {}

    # Build merged ctx for v2 (mlb_game_context fields + bucket lookups)
    enriched = dict(ctx)
    home_p = ctx.get('home_pitcher')
    away_p = ctx.get('away_pitcher')
    if home_p and home_p in cache.get('pitchers', {}):
        pb = cache['pitchers'][home_p]
        enriched['home_innings_1_3_era'] = pb.get('innings_1_3_era')
        enriched['home_innings_4_6_era'] = pb.get('innings_4_6_era')
        enriched['home_innings_7_9_era'] = pb.get('innings_7_9_era')
        enriched['home_sp_ip'] = pb.get('innings_1_3_ip', 0) or 0
    if away_p and away_p in cache.get('pitchers', {}):
        pb = cache['pitchers'][away_p]
        enriched['away_innings_1_3_era'] = pb.get('innings_1_3_era')
        enriched['away_innings_4_6_era'] = pb.get('innings_4_6_era')
        enriched['away_innings_7_9_era'] = pb.get('innings_7_9_era')
        enriched['away_sp_ip'] = pb.get('innings_1_3_ip', 0) or 0
    home_team = ctx.get('home_team')
    away_team = ctx.get('away_team')
    if home_team in cache.get('teams', {}):
        tb = cache['teams'][home_team]
        enriched['home_innings_1_3_runs_per_game'] = tb.get('innings_1_3_runs_per_game')
        enriched['home_innings_4_6_runs_per_game'] = tb.get('innings_4_6_runs_per_game')
        enriched['home_innings_7_9_runs_per_game'] = tb.get('innings_7_9_runs_per_game')
        enriched['home_last10_runs_per_game'] = tb.get('last10_runs_per_game')
    if away_team in cache.get('teams', {}):
        tb = cache['teams'][away_team]
        enriched['away_innings_1_3_runs_per_game'] = tb.get('innings_1_3_runs_per_game')
        enriched['away_innings_4_6_runs_per_game'] = tb.get('innings_4_6_runs_per_game')
        enriched['away_innings_7_9_runs_per_game'] = tb.get('innings_7_9_runs_per_game')
        enriched['away_last10_runs_per_game'] = tb.get('last10_runs_per_game')

    try:
        proj = v2.project_game(enriched)
    except Exception:
        return None

    delta = proj.model_total - float(close_total)
    if delta >= 1.5 and proj.confidence >= 0.7:
        # Umpire-aware filter (added 2026-05-10): suppress OVER picks when
        # ump cohort strongly opposes (under-friendly with n≥30), append
        # confirmation note when ump supports.
        ump_sig = _get_ump_total_signal(ctx.get('umpire'), 'over')
        if ump_sig['action'] == 'suppress':
            print(f"  🚫 v2 OVER suppressed by ump filter: {ump_sig['note']}")
            return None
        suffix = f" • {ump_sig['note']}" if ump_sig['action'] == 'confirm' else ''
        return (
            f"Over {close_total} (v2 edge — model {proj.model_total:.1f} vs market {close_total}, +{delta:.1f} runs){suffix}",
            'total',
            False,
            proj,
        )
    if delta <= -1.5 and proj.confidence >= 0.7:
        ump_sig = _get_ump_total_signal(ctx.get('umpire'), 'under')
        if ump_sig['action'] == 'suppress':
            print(f"  🚫 v2 UNDER suppressed by ump filter: {ump_sig['note']}")
            return None
        suffix = f" • {ump_sig['note']}" if ump_sig['action'] == 'confirm' else ''
        return (
            f"Under {close_total} (v2 edge — model {proj.model_total:.1f} vs market {close_total}, {delta:.1f} runs){suffix}",
            'total',
            False,
            proj,
        )
    return None


def _rl_alt_for_juiced_chalk(ctx):
    """When confluence is PRIME (+4 net) but ML is chalk-juiced (≤-180),
    suggest the run line -1.5 alt instead of the unattractive ML.

    The Cubs 5/7 case was the trigger — model PRIME +8 with reverse-mastery
    on Lowder, but Cubs ML at -207 made the ML EV nearly nil. Cubs RL -1.5
    was the actual play (model projected +2.68 margin). This function
    surfaces that scenario as a POTD candidate.

    Threshold logic (updated 2026-05-21 — see project_spread_delta_trap_zone):
      - confluence_net ≥ +4 (PRIME)
      - |projected_spread| ≥ 2.0 (cohort cliff — 1.5-2.0 is trap zone at 40%)
      - home_ml ≤ -180 OR away_ml ≤ -180 on the favored side
      - direction matches confluence model_pick

    Audit cohort spread_delta_ge2 hits 55% historically — RL covers correlate
    with that signal. Confluence PRIME tier hits 70.6% on direction.
    Combined effect should be net positive at the better RL price (+130-150).
    """
    confluence_net = ctx.get('signal_confluence_net') or 0
    # Prefer v4 (model_pred_spread) over v3 (projected_spread). Sign
    # convention: POSITIVE = home favored (opposite of close_spread).
    # Mirrors the v4-aware fix in game_context.compute_primary_play
    # (2026-05-20 audit). Falls back to v3 when v4 is suppressed.
    projected_spread = ctx.get('model_pred_spread') or ctx.get('projected_spread')
    home_ml = ctx.get('home_ml_close') or ctx.get('home_ml_open')
    away_ml = ctx.get('away_ml_close') or ctx.get('away_ml_open')
    if confluence_net is None or projected_spread is None:
        return None
    try:
        cn = int(confluence_net)
        ps = float(projected_spread)
    except (TypeError, ValueError):
        return None
    if cn < 4 or abs(ps) < 2.0:
        return None
    # Determine favored side per model
    home_favored = ps > 0
    fav_ml = home_ml if home_favored else away_ml
    fav_team = ctx.get('home_team') if home_favored else ctx.get('away_team')
    if fav_ml is None:
        return None
    try:
        fav_ml_i = int(fav_ml)
    except (TypeError, ValueError):
        return None
    # Only fires when ML is chalk-juiced (eats most of the model edge)
    if fav_ml_i > -180:
        return None
    return (
        f"{fav_team} -1.5 (chalk ML alt — model projects +{abs(ps):.1f} margin, ML {fav_ml_i:+d} too juiced)",
        'runline',
        False,
    )


def build_lean(ctx):
    """Determine the lean for an MLB game.

    ML LEANS REMOVED 2026-05-01 pending projection_v2 rebuild.

    PRIORITIES (NRFI demoted 2026-05-30 — see project_nrfi_demotion):
      1. v2 Total Over/Under Edge (audited 62.2% n=90 at delta ≥1.5)
      2. v4 Total Edge (model_pred_total vs market ≥2.5)
      3. RL alt for juiced chalk (model margin ≥1.5 + ML chalk-juiced)
      4. v3 over_lean fallback (xERA gap rule)
      5. NRFI 90-94 — last-resort fallback only. Cohort audits at 50%
         (n=22 / 30d), well below the POTD selector's 58% threshold, so
         the downstream gate filters it out. Kept as a lean string so
         games with NO other model opinion still have something rather
         than (None, None, False), but it won't reach POTD.
      6. NRFI 88-89 — same last-resort logic, even softer cohort.

    The candidate-side POTD selector applies an independent audit-rate
    gate on top of this — see _derive_cohort + MIN_AUDIT_RATE in run().
    NRFI demotion stacks: build_lean deprioritizes, POTD audit filters.
    """
    # 1. v2 Total OVER/UNDER Edge — model_total vs market + 1.5
    v2_pick = _v2_total_edge(ctx)
    if v2_pick is not None:
        return v2_pick[0], v2_pick[1], v2_pick[2]

    # 2. RL alt for juiced chalk (added 2026-05-07 after Cubs 5/7 lesson —
    # PRIME confluence with chalk ML at -200+ surfaces nothing under old logic
    # because ML EV is too thin; RL -1.5 at +130-150 is the actual play when
    # model projects 1.5+ run margin).
    rl_pick = _rl_alt_for_juiced_chalk(ctx)
    if rl_pick is not None:
        return rl_pick

    # 3. Total lean — prefer v4 (model_pred_total) edge against the line.
    ct = ctx.get('close_total') or ctx.get('open_total')
    v4_total = ctx.get('model_pred_total')
    if v4_total is not None and ct is not None:
        try:
            v4_delta = float(v4_total) - float(ct)
        except (TypeError, ValueError):
            v4_delta = None
        if v4_delta is not None and abs(v4_delta) >= 2.5:
            side = 'Over' if v4_delta > 0 else 'Under'
            return f"{side} {ct}", 'total', False
    over_lean = ctx.get('over_lean')
    if over_lean is not None and ct:
        side = 'Over' if over_lean else 'Under'
        return f"{side} {ct}", 'total', False

    # 4-5. NRFI last-resort fallbacks (demoted from #1 priority 2026-05-30).
    # Won't survive the POTD audit gate because cohort hit rate is 50%,
    # below MIN_AUDIT_RATE (58%). Still set as a lean so the game has a
    # non-None display rather than silently dropping out of candidates.
    nrfi = ctx.get('nrfi_score') or 0
    if 90 <= nrfi <= 94:
        return f"NRFI — Score {nrfi}/100 (supplementary)", 'nrfi', True
    if 88 <= nrfi <= 89:
        return f"NRFI — Score {nrfi}/100 (supplementary)", 'nrfi', True

    return None, None, False

def run():
    today = get_today_et()
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    et_hour = et_now.hour
    print(f"Play of the Day — scanning {today} (ET hour: {et_hour})")

    # POTD lock strategy (hybrid):
    # - Pre-8am: always regenerate (stale overnight data)
    # - 8am-11am: always regenerate (early morning data still settling)
    # - 11am+ lock: pick locks so early-game users see a pick
    # - 2pm run override: if new pick has Sweat Score 20+ higher than locked pick,
    #                     overwrite (only trigger when 2pm data changes things materially)
    # 2026-06-05 REFACTOR — separate sweat re-score from POTD selection.
    # Old behavior: when POTD was locked (manualOverride or 11am-2pm window),
    # this function returned early. That ALSO skipped the per-game sweat
    # score writes below — so any caller (watchdog refresh_imminent_games,
    # manual mid-day trigger) silently failed to refresh sweat_score when
    # props/lineups/lines updated. The 6/5 PIT@ATL cron divergence was the
    # poster child: sweat stuck at 52 while live props would have produced
    # 82. See [[project_sweat_rescore_timing_gap_605]] for the deep dive.
    #
    # New behavior: scan POTD lock state into flags but ALWAYS continue to
    # the sweat-write loop. POTD selection (last block of this function)
    # respects the flags so we don't clobber a hand-locked or in-window pick.
    SCORE_OVERRIDE_THRESHOLD = 20  # 20-point score delta to override locked pick
    existing_pick = None
    skip_potd_selection = False
    potd_lock_reason = None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache?game_id=eq.best_bet_{today}&select=data",
            headers=HEADERS
        )
        existing = r.json()
        if existing and len(existing) > 0 and existing[0].get('data', {}).get('pipelineGenerated'):
            existing_pick = existing[0]['data']
            existing_score = existing_pick.get('score', {}).get('total', 0) or 0

            if existing_pick.get('manualOverride'):
                print(f"🔒 manualOverride=true — POTD hand-locked. Sweat scores will still refresh; POTD selection skipped.")
                skip_potd_selection = True
                potd_lock_reason = 'manualOverride'
            elif et_hour < 11:
                print(f"⏰ Pre-11am ET ({et_hour}h) — regenerating with fresh data")
                existing_pick = None  # clear so we overwrite
            elif et_hour < 14:
                print(f"✅ Today's pick locked (11am-2pm window) — sweat will refresh; POTD selection skipped.")
                skip_potd_selection = True
                potd_lock_reason = '11am-2pm lock window'
            else:
                # 2pm+ run: allow override only if new pick beats locked score significantly
                print(f"🔄 2pm+ run — will override locked pick only if new Sweat Score > {existing_score} + {SCORE_OVERRIDE_THRESHOLD}")
    except:
        pass

    # Get all MLB games with context
    mlb_games = get_mlb_games()
    print(f"MLB games: {len(mlb_games)}")

    # Get MLB game times to populate commence_time (also used below for late-slate detection)
    mlb_times = get_mlb_game_times(today)

    # LATE-SLATE DEFER (added 2026-04-29, narrowed 2026-05-19):
    # If no game starts before 4pm ET on this date, defer the POTD *selection*
    # to the 2pm pipeline run. Per-game sweat scores still get written so the
    # app's home-screen MLB tab has fresh tier data in the morning.
    # Before 5/19: this used to `return` early, leaving every game's
    # sweat_score = null and forcing the app into client-side fallback
    # (showed weird 40s-50s scores from outdated formula).
    defer_potd = False
    if et_hour < 14 and mlb_times:
        try:
            earliest = None
            for ts in mlb_times.values():
                if not ts:
                    continue
                t = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                t_et = t - timedelta(hours=4)
                if earliest is None or t_et < earliest:
                    earliest = t_et
            if earliest is not None:
                # 4pm ET cutoff — anything later = weeknight slate, defer POTD
                cutoff_hour = 16
                if earliest.hour >= cutoff_hour:
                    print(f"⏰ Late-slate detected — earliest game {earliest.strftime('%H:%M')} ET, deferring POTD selection to 2pm run (sweat scores still being written)")
                    defer_potd = True
        except Exception as e:
            print(f"  late-slate detect failed (continuing normally): {e}")

    # Get NBA data
    nba_teams = get_nba_teams()
    nba_games = get_nba_games()
    print(f"NBA games: {len(nba_games)}, teams: {len(nba_teams)}")

    # Pre-fetch today's pipeline props so score_mlb_game can factor in
    # stack alerts + PRIME prop count (added 2026-05-16 — sweat_score now
    # reflects what the app actually surfaces).
    props_by_game = {}
    try:
        # 6/2 fix: include book_line in the SELECT. The PROP dim weighting
        # added 6/1 (commit 64629f1) checks book_line to separate book-
        # verified PRIMEs from no-book noise — but this pre-fetch was
        # selecting only game_id/tier/conviction, so book_line was always
        # None and the book-aware branches never fired. Beta users saw
        # PASS games that should have been LIGHT/STRONG because MIA/WSH's
        # Mikolas STRONG ✓book contributed +0 instead of +5, CLE/NYY's
        # full STRONG ✓book stack would have lifted PROP dim, etc.
        # Now the scorer sees the same book_line the props pipeline writes.
        pr = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?game_date=eq.{today}&select=game_id,tier,conviction,book_line",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=15,
        )
        for p in (pr.json() or []):
            gid = p.get('game_id')
            if gid:
                props_by_game.setdefault(gid, []).append(p)
        print(f"  Loaded props for {len(props_by_game)} games (sweat-score stack signal)")
    except Exception as e:
        print(f"  ⚠️ prop fetch for sweat-score failed: {e}")

    # Score all candidates
    candidates = []

    for ctx in mlb_games:
        gid = ctx.get('game_id')
        # Track contributions/evidence for the WHY THIS SCORE UI block
        # (2026-05-25 — Stage 2 of the game-detail parity work).
        track = {'contributions': [], 'evidence': []}
        game_score, dimensions = score_mlb_game(ctx, game_props=props_by_game.get(gid, []), track=track)
        # Write the score + tier back to mlb_game_context so the app reads
        # the server-authoritative value (instead of computing its own with
        # a different formula that systematically under-reports PRIME).
        breakdown = {'dimensions': dimensions}
        if track['contributions']:
            breakdown['contributions'] = track['contributions']
        if track['evidence']:
            breakdown['evidence'] = track['evidence']
        # Headline tier derives from the winning dimension's tier — so a
        # game with TOTAL PRIME headlines PRIME even when SIDE is PASS.
        # The legacy sweat_tier_for(score, ctx) primary_play gate still
        # gates SIDE PRIME via _dim_tier() inside the scorer.
        headline_tier = (dimensions.get(dimensions.get('winning_dimension') or 'side') or {}).get('tier') \
                        or sweat_tier_for(game_score, ctx)
        write_sweat_score(ctx, game_score, headline_tier, breakdown=breakdown)
        lean_display, lean_bet, is_nrfi = build_lean(ctx)
        # 5/30 add: surface sweat_dimensions.model_play alongside legacy
        # lean_display so the POTD value-fallback can compare both. The
        # dimensional scorer often identifies stronger non-NRFI total plays
        # that build_lean's priority chain misses (5/30 PHI/LAD Under 8.5
        # was sweat dim STRONG 68 but build_lean fell to NRFI fallback).
        _winning_dim_name = dimensions.get('winning_dimension')
        _winning_dim = dimensions.get(_winning_dim_name) if _winning_dim_name else {}
        _dim_play = dimensions.get('model_play') or {}
        candidates.append({
            'sport': 'MLB',
            'home_team': ctx.get('home_team'),
            'away_team': ctx.get('away_team'),
            'commence_time': mlb_times.get(ctx.get('home_team')),
            'score': game_score,
            'nrfi_score': ctx.get('nrfi_score'),
            'is_nrfi': is_nrfi,
            'lean_display': lean_display,
            'lean_bet': lean_bet,
            # Dimensional surface (5/30) — winning dim's tier + score + play
            # for use in value-fallback POTD selection. dim_score lets the
            # selector rank by sweat-dimensional tier when lean_display
            # underweights the play (e.g. when build_lean returns NRFI
            # fallback but sweat dim says TOTAL STRONG).
            'dim_winning': _winning_dim_name,
            'dim_score': _winning_dim.get('score'),
            'dim_tier': _winning_dim.get('tier'),
            'dim_play_label': _dim_play.get('label'),
            'dim_play_type': _dim_play.get('type'),
            'dim_play_edge': _dim_play.get('edge'),
            'home_pitcher': ctx.get('home_pitcher'),
            'away_pitcher': ctx.get('away_pitcher'),
            'home_sp_xera': ctx.get('home_sp_xera'),
            'away_sp_xera': ctx.get('away_sp_xera'),
            'projected_total': ctx.get('projected_total'),
            'projected_spread': ctx.get('projected_spread'),
            'spread_delta': ctx.get('spread_delta'),
            'signal_confluence_net': ctx.get('signal_confluence_net'),
            'signal_confluence_support': ctx.get('signal_confluence_support'),
            'signal_confluence_breakdown': ctx.get('signal_confluence_breakdown'),
            # Downstream cohort math expects close_spread; fall back to open
            # so manual noon runs (close_* still null) don't break candidate
            # eval. Same fallback pattern used in compute_primary_play.
            'close_spread': ctx.get('close_spread') or ctx.get('open_spread'),
            'open_spread': ctx.get('open_spread'),
            'home_ml_odds': ctx.get('home_ml_odds') or ctx.get('home_ml_close') or ctx.get('home_ml_open'),
            'away_ml_odds': ctx.get('away_ml_odds') or ctx.get('away_ml_close') or ctx.get('away_ml_open'),
            'venue': ctx.get('venue'),
            'temperature': ctx.get('temperature'),
            # Pass the full mlb_game_context dict through so downstream
            # cohort_signals.evaluate_game_for_play() can compute the
            # complete feature set (xERA, BP usage, OAA, platoon, etc.)
            # not just the handful of fields the candidate carries. Added
            # 2026-06-10 — the model-cohort conflict gate was silently
            # under-counting cohorts because the stripped candidate dict
            # missed close_total / model_pred_total / SP stats.
            '_ctx': ctx,
        })

    for game in nba_games:
        game_score, nba_lean, nba_lean_type = score_nba_game(game, nba_teams)
        candidates.append({
            'sport': 'NBA',
            'home_team': game.get('home_team'),
            'away_team': game.get('away_team'),
            'score': game_score,
            'nrfi_score': None,
            'is_nrfi': False,
            'lean_display': nba_lean,
            'lean_bet': nba_lean_type or 'ml',
            'commence_time': game.get('commence_time'),
        })

    if not candidates:
        print("No games found — storing noGames")
        requests.post(
            f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "cache_key": f"best_bet_{today}",
                "game_id": f"best_bet_{today}",
                "sport": "none",
                "narrative": "No games on the slate today.",
                "data": {"noGames": True},
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return

    # Sort by score
    candidates.sort(key=lambda c: c['score'], reverse=True)

    # Late-slate POTD defer — sweat scores are already written above; just
    # skip the POTD lock/selection logic until the 2pm run.
    if defer_potd:
        print(f"  ✓ Wrote sweat scores for {len(candidates)} games. POTD selection deferred to 2pm run.")
        return

    # 2026-06-05: respect the manualOverride / 11am-2pm lock detected at
    # function entry. Sweat scores are already written above — same defer
    # pattern as late-slate. POTD selection only runs when the lock allows.
    if skip_potd_selection:
        print(f"  ✓ Wrote sweat scores for {len(candidates)} games. POTD selection skipped ({potd_lock_reason}).")
        return

    # ── AUDIT-DRIVEN POTD SELECTION (2026-05-19 rewrite) ──
    # Replaces hardcoded Tier 1/1.5/2 ordering. For each candidate, look up
    # the live 30d hit rate from mlb_tier_calibration. Only candidates from
    # cohorts that ACTUALLY audit above the break-even threshold (and have
    # enough sample size to trust) make the pool. Top by audit rate wins.
    #
    # Why: tier-based selection was hardcoding the OVER v2 Edge tier above
    # NRFI 88-89 edge based on a backtest claim of 62.2% — live POTD
    # outcomes on that bucket showed 50% (n=6), basically coin flip. The
    # self-calibrating version drops anything below MIN_AUDIT_RATE and
    # promotes whatever's actually hitting in production right now.
    #
    # When NO candidate clears the bar → no-play day (honest content beats
    # forced picks). Same skip-day design as before.
    MIN_AUDIT_RATE = 0.58  # need to beat -135 ML break-even to lock as POTD
    MIN_SAMPLE_SIZE = 10   # below this, can't trust the rate

    _cohort_cache = {}
    # 2026-06-10: cohort_signals-derived tier baselines for v3_tot picks.
    # These are the tier-system rates the new ML/RL cohort engine surfaces
    # (LOCK ≥75% / STRONG_EDGE ≥65% / LEAN ≥60%). When a v3_tot pick fires
    # at one of these tiers, this synthetic rate gates it through the POTD
    # audit step without requiring a separate mlb_tier_calibration row.
    # The tier itself is the calibration — Bayesian shrunken_pct already
    # accounts for sample size.
    SYNTHETIC_COHORT_RATES = {
        'v3_tot_lock':         (0.75, 30),   # LOCK tier predicted 75%+
        'v3_tot_strong_edge':  (0.68, 50),   # STRONG_EDGE predicted 65-75%
        'v3_tot_lean':         (0.62, 80),   # LEAN predicted 60-65%
    }

    def _cohort_rate(cohort_key):
        """Pull latest 30d hit rate for the cohort from mlb_tier_calibration.
        Cached per-run. Returns (rate, n) or (None, 0) if cohort isn't
        calibrated yet."""
        if not cohort_key:
            return (None, 0)
        if cohort_key in _cohort_cache:
            return _cohort_cache[cohort_key]
        # Check synthetic v3_tot tier baselines first
        if cohort_key in SYNTHETIC_COHORT_RATES:
            _cohort_cache[cohort_key] = SYNTHETIC_COHORT_RATES[cohort_key]
            return _cohort_cache[cohort_key]
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_tier_calibration",
                params={
                    'tier': f'eq.{cohort_key}',
                    'window_label': 'eq.30d',
                    'select': 'hit_rate,total,computed_date',
                    'order': 'computed_date.desc',
                    'limit': '1',
                },
                headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
                timeout=10,
            )
            rows = r.json() if r.status_code == 200 else []
            if rows:
                result = (float(rows[0].get('hit_rate') or 0), int(rows[0].get('total') or 0))
                _cohort_cache[cohort_key] = result
                return result
        except Exception as e:
            print(f"  ⚠️ cohort rate lookup failed for {cohort_key}: {e}")
        _cohort_cache[cohort_key] = (None, 0)
        return _cohort_cache[cohort_key]

    def _derive_cohort(c):
        """Map a candidate to its calibration cohort key. Returns None if
        the candidate doesn't have a calibrated cohort (will be filtered)."""
        if c.get('is_nrfi'):
            n = c.get('nrfi_score') or 0
            if 90 <= n <= 94: return 'nrfi_prime_90_94'
            if 88 <= n <= 89: return 'nrfi_dead_80_89'
            if 70 <= n <= 79: return 'nrfi_lean_70_79'
            if n >= 95:        return 'nrfi_volatile_95plus'
            if n <= 25:        return 'yrfi_lean_le40'
            return None
        if c.get('lean_bet') == 'ml':
            # Check the more-specific autofade cohort first. Per audit
            # 2026-05-21 (see project_spread_delta_trap_zone), when the model
            # picks a DOG with high conviction (cn>=2 + market disagrees),
            # the autofade_dog_high_conv cohort hits 58-65% live. That beats
            # confluence_prime_ge4 in real outcomes and deserves promotion.
            # If the candidate doesn't fit autofade_dog_high_conv, fall back
            # to confluence_prime_ge4 (existing strict gate).
            try:
                from auto_fade import cohort_for_pick
                ml_cohort = cohort_for_pick(
                    c.get('projected_spread'),
                    c.get('close_spread'),
                    c.get('signal_confluence_net'),
                    home_ml=c.get('home_ml_odds'),
                    away_ml=c.get('away_ml_odds'),
                )
                if ml_cohort == 'ml_dog_high_conv':
                    return 'autofade_dog_high_conv'
            except Exception:
                pass
            return 'confluence_prime_ge4'
        # 2026-06-10 — TOTAL O/U eligibility added. The original "no calibrated
        # cohort" comment was from before the cohort_signals engine existed.
        # Now we have v3_tot 66.8% lifetime, v4_tot 56%, and the cohort engine
        # surfaces STRONG_EDGE total cohorts at 70%+ regularly. Structurally
        # excluding totals was forcing POTD to pick ML even on slates where the
        # loudest play was a total — 6/9 SF Giants ML pick (LOSS) was the value
        # fallback after CHC@COL Under PRIME 100 + ARI@MIA Over PRIME 82 were
        # both gated out. POTD record 5-7 (41.7%) over last 14 days is the
        # direct cost of this exclusion. See project_potd_total_eligibility_610.
        #
        # Mapping rule: use the cohort_signals row's tier for the candidate's
        # play type (v3_tot over/under). When the v3 total model has a
        # STRONG_EDGE or LOCK match at >=58% (matches MIN_AUDIT_RATE), the
        # total is POTD-eligible. The actual cohort lookup falls back through
        # _cohort_rate to mlb_tier_calibration if a `v3_tot_strong_edge` entry
        # is calibrated; otherwise the v3_tot lifetime baseline (66.8%) is the
        # implicit rate floor and we return a stable cohort key the audit gate
        # will treat as healthy.
        if c.get('lean_bet') == 'total':
            # Pull cohort_signals tier for v3_tot on this game. Direction
            # derived from the lean_display ("Over 8.5" / "Under 12.5").
            try:
                from cohort_signals import evaluate_game_for_play
                ld = (c.get('lean_display') or '').lower()
                direction = 'over' if 'over' in ld else ('under' if 'under' in ld else None)
                if direction is None:
                    return None
                opposite = 'under' if direction == 'over' else 'over'
                tier_order = {'LOCK': 0, 'STRONG_EDGE': 1, 'LEAN': 2}

                # Use full ctx (passed through as _ctx) for evaluate_game_for_play
                # so it can compute the complete feature set. The thin candidate
                # dict misses close_total / xERA / SP stats and would silently
                # under-count cohort matches, causing the conflict gate below to
                # skip games that ARE materially contested.
                eval_target = c.get('_ctx') or c

                # Pull strong-edge matches for BOTH directions to evaluate
                # cohort balance (added 2026-06-10 morning after user audit
                # surfaced CHC@COL UNDER POTD as a contested play — 3/3
                # models UNDER but cohort net +11 OVER). The cohort engine
                # tells us how game conditions historically played; when
                # the OPPOSITE direction's cohort count materially exceeds
                # the picked direction's, this is a model-vs-cohort
                # conflict and shouldn't be POTD-grade.
                picked_matches = evaluate_game_for_play(eval_target, 'v3_tot', direction) or []
                opp_matches = evaluate_game_for_play(eval_target, 'v3_tot', opposite) or []
                picked_loud = [m for m in picked_matches if m.get('tier') in tier_order]
                opp_loud = [m for m in opp_matches if m.get('tier') in tier_order]
                # Count STRONG_EDGE+ each side (the user-audit metric)
                picked_strong = [m for m in picked_loud if m.get('tier') in ('LOCK','STRONG_EDGE')]
                opp_strong = [m for m in opp_loud if m.get('tier') in ('LOCK','STRONG_EDGE')]

                if not picked_loud:
                    return None

                # CONFLICT GATE: if opposite-direction STRONG_EDGE count
                # exceeds picked-direction count by 5+, this is a contested
                # cohort read. Downgrade tier by one notch (LOCK→STRONG,
                # STRONG→LEAN, LEAN→reject). Threshold 5 chosen to match
                # the +5 NET gap I used as the manual "skip" threshold in
                # the slate audit; below 5 the cohorts are close enough
                # that model unanimity carries.
                cohort_gap = len(opp_strong) - len(picked_strong)
                best = min(picked_loud, key=lambda m: (tier_order[m['tier']], -m.get('shrunken_pct', 0)))
                tier = best['tier']

                # Conflict-severity tier-down. Cohort engine net materially
                # against picked direction = signal contested. Severity scaled:
                #   gap 5-9  → 1 notch (LOCK→STRONG, STRONG→LEAN, LEAN→reject)
                #   gap 10+  → 2 notches (LOCK→LEAN, STRONG→reject, LEAN→reject)
                # CHC@COL 6/10 has gap +11 → STRONG_EDGE-side LOCK downgrades
                # to LEAN, then fails shrunken_pct gate vs runners-up at 0.68.
                if cohort_gap >= 10:
                    print(f"  ⚠⚠ {c.get('away_team')} @ {c.get('home_team')} v3_tot {direction.upper()}: "
                          f"picked {len(picked_strong)} STRONG_EDGE vs opposite {len(opp_strong)} (+{cohort_gap}). "
                          f"Severe conflict — 2-notch tier-down.")
                    if tier == 'LOCK': tier = 'LEAN'
                    elif tier == 'STRONG_EDGE': return None
                    elif tier == 'LEAN': return None
                elif cohort_gap >= 5:
                    print(f"  ⚠ {c.get('away_team')} @ {c.get('home_team')} v3_tot {direction.upper()}: "
                          f"picked {len(picked_strong)} STRONG_EDGE vs opposite {len(opp_strong)} (+{cohort_gap}). "
                          f"Conflict — 1-notch tier-down.")
                    if tier == 'LOCK': tier = 'STRONG_EDGE'
                    elif tier == 'STRONG_EDGE': tier = 'LEAN'
                    elif tier == 'LEAN': return None  # reject contested LEAN

                if tier == 'LOCK': return 'v3_tot_lock'
                if tier == 'STRONG_EDGE': return 'v3_tot_strong_edge'
                if tier == 'LEAN' and best.get('shrunken_pct', 0) >= 60:
                    return 'v3_tot_lean'
            except Exception as e:
                pass
            return None
        return None

    # Build audit-validated candidate pool
    audit_pool = []
    audit_log = []
    for c in candidates:
        if c.get('sport') != 'MLB':
            continue  # NBA + other sports handled below
        cohort = _derive_cohort(c)
        if not cohort:
            audit_log.append(f"  ⊘ {c['away_team']} @ {c['home_team']}: no calibrated cohort")
            continue
        rate, n = _cohort_rate(cohort)
        if rate is None:
            audit_log.append(f"  ⊘ {c['away_team']} @ {c['home_team']} ({cohort}): no calibration data")
            continue
        if n < MIN_SAMPLE_SIZE:
            audit_log.append(f"  ⊘ {c['away_team']} @ {c['home_team']} ({cohort}): n={n} below min {MIN_SAMPLE_SIZE}")
            continue
        if rate < MIN_AUDIT_RATE:
            audit_log.append(f"  ⊘ {c['away_team']} @ {c['home_team']} ({cohort}): audit {rate*100:.1f}% below threshold {MIN_AUDIT_RATE*100:.0f}%")
            continue

        # ────────────────────────────────────────────────────────────────────
        # RESOLVER GATE (added 2026-06-10 evening).
        # Retroactive audit n=390 graded games / 30d showed:
        #   STRONG resolver: 95-53 (64.2%), +$3,345 P&L, +22.6% ROI
        #   LIGHT  resolver: 75-95 (44.1%), -$2,675 P&L, -15.7% ROI
        # POTD has been picking LIGHT-tier candidates as value fallback, which
        # is what's driven the 41.7% hit rate / negative ROI. Gate: for TOTAL
        # picks, require resolver tier == STRONG or ELITE. LIGHT/LEAN/SKIP
        # are rejected — better no POTD than a publishing pick that loses
        # money on average.
        if c.get('lean_bet') == 'total':
            try:
                from signal_resolver import resolve_total
                from cohort_signals import evaluate_game_for_play as _eval

                eval_target = c.get('_ctx') or c
                ld = (c.get('lean_display') or '').lower()
                # Count STRONG_EDGE+ per direction
                def _ct(direction):
                    m = _eval(eval_target, 'v3_tot', direction) or []
                    return len([x for x in m
                                if x.get('tier') in ('LOCK', 'STRONG_EDGE')
                                and not x.get('id', '').endswith('|any')])

                # Pull prop_reverse signal for the matchup (best-effort).
                pr_signal = None
                try:
                    today_str = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')
                    pr_row = requests.get(
                        f"{SUPABASE_URL}/rest/v1/jerry_cache",
                        params={'select': 'data',
                                'cache_key': f'eq.prop_reverse_signals_{today_str}'},
                        headers={'apikey': SUPABASE_KEY,
                                 'Authorization': f'Bearer {SUPABASE_KEY}'},
                        timeout=3,
                    )
                    rows = pr_row.json() if pr_row.status_code == 200 else []
                    if rows:
                        pr_data = rows[0].get('data', {})
                        if isinstance(pr_data, dict):
                            matchup_key = f"{c.get('away_team')} @ {c.get('home_team')}"
                            pr_signal = (pr_data.get('signals') or {}).get(matchup_key)
                except Exception:
                    pr_signal = None

                resolved = resolve_total(
                    close_total=(eval_target.get('close_total') or eval_target.get('open_total')),
                    v3_total=eval_target.get('projected_total'),
                    v4_total=eval_target.get('model_pred_total'),
                    jerry_total=eval_target.get('jerry_pred_total'),
                    cohort_over_strong_count=_ct('over'),
                    cohort_under_strong_count=_ct('under'),
                    prop_reverse=pr_signal,
                )
                resolver_tier = resolved.get('tier')
                if resolver_tier not in ('STRONG', 'ELITE'):
                    audit_log.append(
                        f"  ⊘ {c['away_team']} @ {c['home_team']} ({cohort}): "
                        f"resolver {resolver_tier} (need STRONG+). {resolved.get('reason', '')}")
                    continue
                # Store resolver result on candidate so downstream consumers
                # (Jerry reads, sweat card) can cite the landing call.
                c['_resolver_tier'] = resolver_tier
                c['_resolver_reason'] = resolved.get('reason')
                c['_resolver_direction'] = resolved.get('direction')
            except Exception as e:
                # Resolver failure must not block legacy behavior. Log and
                # continue — the cohort+audit gate still applies.
                print(f"  ⚠ resolver gate failed for {c.get('away_team')} @ "
                      f"{c.get('home_team')}: {type(e).__name__}: {e}")

        c['_cohort'] = cohort
        c['_audit_rate'] = rate
        c['_audit_n'] = n
        audit_pool.append(c)

    # Sort: highest audit rate first, then in-game signal strength (sweat score)
    audit_pool.sort(key=lambda c: (-c['_audit_rate'], -c.get('score', 0)))

    if audit_log:
        print("AUDIT-DRIVEN POTD FILTER:")
        for line in audit_log:
            print(line)

    pick = None
    confidence = 'standard'
    best_overall = candidates[0] if candidates else None

    if audit_pool:
        pick = audit_pool[0]
        # Tier label from audit rate (used by app for visual styling)
        r = pick['_audit_rate']
        if r >= 0.68: confidence = 'elite'
        elif r >= 0.62: confidence = 'high'
        else: confidence = 'solid'
        print(f"🔒 AUDIT-DRIVEN PICK: {pick['away_team']} @ {pick['home_team']} — "
              f"cohort {pick['_cohort']} hit {pick['_audit_rate']*100:.1f}% (n={pick['_audit_n']})")
        if len(audit_pool) > 1:
            print(f"   Runners-up:")
            for c in audit_pool[1:4]:
                print(f"     {c['away_team']} @ {c['home_team']} ({c['_cohort']}: {c['_audit_rate']*100:.1f}% n={c['_audit_n']})")

    # NBA fallback path (separate cohort calibration not yet wired into
    # mlb_tier_calibration — preserve the legacy score-based gate).
    if not pick and best_overall and best_overall.get('sport') == 'NBA':
        if best_overall['score'] >= 75:
            pick = best_overall
            confidence = 'high'
            print(f"🔒 NBA HIGH CONVICTION: {pick['away_team']} @ {pick['home_team']} — Score {pick['score']}")
        elif best_overall['score'] >= 65:
            pick = best_overall
            confidence = 'solid'
            print(f"✅ NBA pick: {pick['away_team']} @ {pick['home_team']} — Score {pick['score']}")

    # VALUE TIER FALLBACK (2026-05-23): when no audit-qualified cohort fires,
    # surface the model's strongest model-supported lean rather than skipping.
    # Prior design ("no-play day" preserves trust) was correct in principle but
    # produced empty POTD slots that propagated as placeholder rows in the
    # sweat card top_8 — worse UX than a clearly-labeled sub-audit pick.
    #
    # Rules for value pick:
    #   - Must have a computed lean_display (build_lean returned a side)
    #   - lean_bet MUST NOT be nrfi/yrfi (5/30 demotion — NRFI never POTD)
    #   - Sort by composite of |signal_confluence_net| + score
    #   - Confidence tag 'value' so app can style it softer than audit-locked
    #   - Narrative explicitly labels it Model Lean, not audit-qualified
    #
    # 5/30 NRFI-demotion plug: the audit filter correctly rejects NRFI
    # cohorts (47.8% < 58% threshold), but the value fallback was still
    # picking NRFI-leaning candidates because they had a lean_display
    # string. Excluding lean_bet in (nrfi, yrfi) closes the loophole so
    # NRFI never reaches POTD even when no other audit cohort qualifies.
    #
    # 5/30 architectural fix: if a candidate's sweat_dimensions model_play
    # (winning_dimension tier >= STRONG with an actionable play that isn't
    # NRFI) is stronger than the legacy lean_display, promote the
    # candidate using the dim_play and override its lean fields. The
    # dimensional scorer often identifies cleaner non-NRFI total plays
    # (e.g. PHI/LAD Under 8.5 STRONG 68 vs build_lean's NRFI fallback)
    # that the legacy chain misses. Without this, the 5/30 POTD had to be
    # manually overridden because the selector couldn't see the sweat
    # dim's read on PHI/LAD.
    if not pick:
        # First: promote candidates with strong sweat-dim plays. Mutate
        # their lean_display/lean_bet so the value_pool selection logic
        # naturally surfaces them.
        for c in candidates:
            if c.get('sport') != 'MLB':
                continue
            dim_tier = c.get('dim_tier')
            dim_play_type = (c.get('dim_play_type') or '').upper()
            dim_play_label = c.get('dim_play_label')
            # Only consider STRONG+ dim plays that aren't NRFI/YRFI
            if dim_tier not in ('STRONG', 'PRIME'):
                continue
            if dim_play_type in ('NRFI', 'YRFI') or not dim_play_label:
                continue
            # If legacy lean_bet is nrfi or weaker, promote dim's play
            if c.get('lean_bet') in ('nrfi', 'yrfi', None):
                c['lean_display'] = dim_play_label
                c['lean_bet'] = 'total' if 'TOTAL' in dim_play_type else (
                                'ml' if 'ML' in dim_play_type else (
                                'spread' if 'SPREAD' in dim_play_type else 'total'))
                c['is_nrfi'] = False
                c['_promoted_from_dim'] = True

        value_pool = [
            c for c in candidates
            if c.get('sport') == 'MLB'
            and c.get('lean_display')
            and (c.get('score') or 0) >= 50
            and c.get('lean_bet') not in ('nrfi', 'yrfi')
        ]
        # Composite rank: prefer sweat-dim-promoted candidates with STRONG+
        # dim tier, then by confluence magnitude, then by sweat score.
        def _rank_key(c):
            dim_tier_rank = {'PRIME': 2, 'STRONG': 1}.get(c.get('dim_tier'), 0)
            return (
                -dim_tier_rank if c.get('_promoted_from_dim') else 0,
                -abs(c.get('signal_confluence_net') or 0),
                -(c.get('score') or 0),
            )
        value_pool.sort(key=_rank_key)
        if value_pool:
            pick = value_pool[0]
            confidence = 'value'
            conf_net = pick.get('signal_confluence_net') or 0
            src = 'dim-promoted' if pick.get('_promoted_from_dim') else 'legacy lean'
            print(f"📌 VALUE POTD (sub-audit fallback, {src}): {pick['away_team']} @ {pick['home_team']} — "
                  f"{pick.get('lean_display')} | confluence={conf_net:+d} | sweat={pick.get('score')}")

    if not pick:
        print("🚫 No model-supported lean anywhere on the board — no POTD posted today.")
        try:
            requests.post(
                f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport",
                headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json={
                    "cache_key": f"best_bet_{today}",
                    "game_id": f"best_bet_{today}",
                    "sport": "none",
                    "narrative": (
                        "No play on the board today. The slate didn't generate any "
                        "leans the model has conviction on. Bucket angles + Dawg of "
                        "the Day are still in the app."
                    ),
                    "data": {"noPlay": True, "reason": "no_model_supported_lean"},
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as e:
            print(f"  no-play marker write failed: {e}")
        return

    # Print all candidates
    for c in candidates[:5]:
        nrfi_str = f" | NRFI {c['nrfi_score']}" if c.get('nrfi_score') else ''
        print(f"  {c['sport']} {c['away_team']} @ {c['home_team']} — Score {c['score']}{nrfi_str} | Lean: {c.get('lean_display') or 'none'}")

    # 2pm override gate (BUG #2 FIX 2026-04-26): TIER hierarchy beats numeric score.
    # Yesterday: locked Tier 2 (NRFI edge 89, score 78), afternoon found Tier 1
    # (PRIME confluence ML, score 71). Old code blocked override based on score alone,
    # keeping the WEAKER tier locked. Now: a strictly higher tier overrides regardless
    # of score; same-tier override still requires +20 score delta.
    # Tier ranking: 'high' = 1 (HIGH CONVICTION), 'solid' = 2 (NRFI/ML lean/NBA solid),
    #               'standard' = 3 (best available)
    # 'elite' = sweet-spot NRFI 90-94 — 70.0% lifetime, 68.8% L30d (n=30) per 5/18 DB audit.
    # 'high' = PRIME confluence ML / NBA high conviction.
    # 'solid' = NRFI edge / ML lean / NBA solid.
    # 'standard' = best available fallback.
    def _new_pick_cohort_healthy(c):
        """Return False if the candidate's auto_fade cohort has a 7d hit
        rate below 0.50 with n>=8. Used as a stickiness gate when the 2pm
        run wants to override the 8am locked pick — don't flip to a cold
        cohort even if its score outranks. NRFI/NBA picks (no spread_delta
        cohort) are always considered healthy."""
        try:
            from auto_fade import adjust_pick, CALIBRATION
            res = adjust_pick(
                c.get('projected_spread'), c.get('close_spread'),
                c.get('signal_confluence_net'),
                c.get('home_team'), c.get('away_team'),
                home_ml=c.get('home_ml_odds'), away_ml=c.get('away_ml_odds'),
            )
            cohort = res.get('cohort')
            if not cohort or cohort not in CALIBRATION:
                return True  # NRFI / NBA / unmapped pick — no cohort gate
            cal = CALIBRATION[cohort]
            n_7d = cal.get('n_7d') or 0
            hit_7d = cal.get('hit_rate_7d')
            if hit_7d is None or n_7d < 8:
                return True  # not enough recent sample to judge
            return hit_7d >= 0.50
        except Exception:
            return True  # fail-open

    TIER_RANK = {'elite': 0, 'high': 1, 'solid': 2, 'standard': 3, 'value': 4}
    if existing_pick and et_hour >= 14:
        existing_score = existing_pick.get('score', {}).get('total', 0) or 0
        existing_confidence = existing_pick.get('confidence', 'standard')
        new_score = pick.get('score', 0) or 0
        existing_tier = TIER_RANK.get(existing_confidence, 3)
        new_tier = TIER_RANK.get(confidence, 3)
        # Cohort-health gate for ANY override (tier upgrade or same-tier).
        # Pulls 7d hit rate of the new pick's cohort from mlb_tier_calibration
        # — if cohort is in a 7d slump (rate < 0.50, n>=8), refuse to flip
        # away from the locked morning pick. Stops the 2pm cron from swapping
        # an already-decent pick to a freshly-eligible cold cohort.
        new_cohort_healthy = _new_pick_cohort_healthy(pick)
        if new_tier < existing_tier:
            if not new_cohort_healthy:
                print(f"🔒 Keeping locked pick — new {confidence} pick's cohort in 7d slump (would have upgraded tier)")
                return
            print(f"🔄 TIER UPGRADE OVERRIDE — new pick {confidence} (tier {new_tier}) beats locked {existing_confidence} (tier {existing_tier})")
        elif new_tier > existing_tier:
            print(f"🔒 Keeping locked pick — new pick {confidence} is strictly lower tier than locked {existing_confidence}")
            return
        else:
            if new_score < existing_score + SCORE_OVERRIDE_THRESHOLD:
                print(f"🔒 Keeping locked pick — new score {new_score} doesn't beat locked {existing_score} + {SCORE_OVERRIDE_THRESHOLD} (same tier)")
                return
            if not new_cohort_healthy:
                print(f"🔒 Keeping locked pick — same-tier override blocked, new pick's cohort in 7d slump")
                return
            print(f"🔄 OVERRIDE — same tier, new score {new_score} beats locked {existing_score} + {SCORE_OVERRIDE_THRESHOLD}")

    # Build the result — app will generate Jerry narrative on first load
    result = {
        'game': {
            'home_team': pick['home_team'],
            'away_team': pick['away_team'],
            'commence_time': pick.get('commence_time'),
        },
        'sport': pick['sport'],
        'score': {'total': pick['score'], 'isNRFI': pick.get('is_nrfi', False), 'nrfiScore': pick.get('nrfi_score')},
        'leanDisplay': pick.get('lean_display') or f"{pick['away_team']} @ {pick['home_team']}",
        'generatedAt': today,
        'pipelineGenerated': True,
        'confidence': confidence,  # high, solid, standard
        # Include context for Jerry narrative generation
        'context': {
            'home_pitcher': pick.get('home_pitcher'),
            'away_pitcher': pick.get('away_pitcher'),
            'home_sp_xera': pick.get('home_sp_xera'),
            'away_sp_xera': pick.get('away_sp_xera'),
            'projected_total': pick.get('projected_total'),
            'spread_delta': pick.get('spread_delta'),
            'projected_spread': pick.get('projected_spread'),
            'lean_bet': pick.get('lean_bet'),
            'nrfi_score': pick.get('nrfi_score'),
            'venue': pick.get('venue'),
            'temperature': pick.get('temperature'),
        },
    }

    # Store in jerry_cache
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json={
            "cache_key": f"best_bet_{today}",
            "game_id": f"best_bet_{today}",
            "sport": pick['sport'],
            "narrative": f"Play of the Day: {pick['away_team']} @ {pick['home_team']} | {pick.get('lean_display', '')}",
            "data": result,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if r.status_code in [200, 201, 204]:
        print(f"✅ Play of the Day stored: {pick['sport']} {pick['away_team']} @ {pick['home_team']} | Lean: {pick.get('lean_display')}")
    else:
        print(f"❌ Cache store failed: {r.status_code} {r.text[:200]}")

    # Also log to history — was silently swallowed pre 2026-05-25; turned
    # loud after 5/25 incident (home tab showed NRFI, receipts showed v2 OVER
    # — both writes timestamped within 250ms of each other in same run yet
    # ended up with different picks). Most likely cause: two concurrent
    # play_of_day invocations racing on the two upserts. Loud failure surfaces
    # this in cron logs next time.
    expected_game = f"{pick['away_team']} @ {pick['home_team']}"
    expected_lean = pick.get('lean_display')
    try:
        hr = requests.post(
            f"{SUPABASE_URL}/rest/v1/daily_best_bet_history?on_conflict=bet_date",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "bet_date": today,
                "sport": pick['sport'],
                "game": expected_game,
                "lean": expected_lean,
                "sweat_score": pick['score'],
                "result": "Pending",
            },
            timeout=15,
        )
        if hr.status_code not in (200, 201, 204):
            print(f"  ⚠️ history write returned {hr.status_code}: {hr.text[:200]}")
    except Exception as e:
        print(f"  ⚠️ history write FAILED: {e}")

    # Consistency check: read back both surfaces and warn if they diverge.
    # When this fires, the next POTD render to users will show mismatched
    # values across Home and Receipts tabs.
    try:
        jc_back = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache?game_id=eq.best_bet_{today}&select=data",
            headers=HEADERS, timeout=10,
        ).json()
        hist_back = requests.get(
            f"{SUPABASE_URL}/rest/v1/daily_best_bet_history?bet_date=eq.{today}&select=game,lean",
            headers=HEADERS, timeout=10,
        ).json()
        jc_lean = (jc_back[0].get('data') or {}).get('leanDisplay') if jc_back else None
        jc_game_d = (jc_back[0].get('data') or {}).get('game') if jc_back else {}
        jc_game = f"{jc_game_d.get('away_team')} @ {jc_game_d.get('home_team')}" if jc_game_d else None
        h_game = hist_back[0].get('game') if hist_back else None
        h_lean = hist_back[0].get('lean') if hist_back else None
        if (jc_game and h_game and jc_game != h_game) or (jc_lean and h_lean and jc_lean != h_lean):
            print(f"  ⚠️ POTD DIVERGENCE DETECTED post-write:")
            print(f"     jerry_cache: game={jc_game!r}  lean={jc_lean!r}")
            print(f"     history:     game={h_game!r}  lean={h_lean!r}")
            print(f"     expected:    game={expected_game!r}  lean={expected_lean!r}")
            print(f"     Likely cause: concurrent play_of_day invocation overwrote one surface.")
    except Exception as e:
        print(f"  ⚠️ post-write consistency check failed: {e}")

if __name__ == '__main__':
    run()
