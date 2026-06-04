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
    conf_net = ctx.get('signal_confluence_net')
    try:
        conf_mag = abs(int(conf_net)) if conf_net is not None else 0
    except (TypeError, ValueError):
        conf_mag = 0
    # 2026-06-03: added 6+ rung. 5+ was the ceiling; on 6/3 LAD @ ARI hit
    # 6-signal confluence with 3.9-run Jerry edge but SIDE capped at 63 because
    # 6 and 5 paid the same. Real 6+ confluence is rarer and warrants its own tier.
    if conf_mag >= 6:
        _add(side_drivers, 18, '🎯', 'Elite confluence', f'{conf_mag} independent signals align')
    elif conf_mag >= 5:
        _add(side_drivers, 14, '🎯', 'PRIME confluence', f'{conf_mag} independent signals align')
    elif conf_mag >= 4:
        _add(side_drivers, 10, '🎯', 'Strong confluence', f'{conf_mag} signals on one side')
    elif conf_mag >= 3:
        _add(side_drivers, 6, '🎯', 'Confluence edge', f'{conf_mag} signals on one side')
    elif conf_mag >= 2:
        _add(side_drivers, 3, '🎯', 'Confluence lean', f'{conf_mag} signals on one side')

    # ---- SIDE: Spread delta — v3 + Jerry (REWORKED 2026-05-31) ----
    # 5/21 audit: v3 alone in 1.5-2.0 band hits ~40-43% (trap). ≥2.0
    # conviction band hits ~55-58%. <1.0 noise. So v3 standalone bands hold.
    # 5/31 add: Jerry (linear deep-factor projection) gets its own band-scored
    # contribution. Different model, real second opinion — credit it
    # independently. When Jerry confirms direction with ≥2.0 magnitude, also
    # rescue v3's trap-zone (1.5-2.0) contribution that the standalone audit
    # punished. Reason: the audit was on v3 vs market only; v3+Jerry agreement
    # in the trap zone is a different (likely better) signal than v3 alone.
    #
    # Sign convention: projected_spread / jerry_pred_spread POSITIVE = home
    # favored. close_spread book-side (negative = home laid). Disagreement
    # magnitude = abs(model_spread + close_spread). Direction sign retained
    # to detect v3↔Jerry agreement for trap-zone rescue.
    close_spread_val = ctx.get('close_spread') or ctx.get('open_spread')
    proj_spread_val = ctx.get('projected_spread')
    jerry_spread_val = ctx.get('jerry_pred_spread')

    v3_signed = None
    if proj_spread_val is not None and close_spread_val is not None:
        try:
            v3_signed = float(proj_spread_val) + float(close_spread_val)
        except (TypeError, ValueError):
            v3_signed = None

    jerry_signed = None
    if jerry_spread_val is not None and close_spread_val is not None:
        try:
            jerry_signed = float(jerry_spread_val) + float(close_spread_val)
        except (TypeError, ValueError):
            jerry_signed = None

    v3_abs = abs(v3_signed) if v3_signed is not None else abs(float(ctx.get('spread_delta') or 0))
    jerry_abs = abs(jerry_signed) if jerry_signed is not None else 0.0

    # v3 contribution (with trap-zone rescue when Jerry confirms direction)
    v3_trap_rescued = False
    if v3_abs >= 2.0:
        _add(side_drivers, 13, '📊', 'v3 market disagreement', f'{v3_abs:.1f}-run v3 vs market')
    elif v3_abs >= 1.5:
        # Trap zone — rescued only if Jerry agrees direction with conviction
        if (v3_signed is not None and jerry_signed is not None
                and v3_signed * jerry_signed > 0 and jerry_abs >= 2.0):
            _add(side_drivers, 6, '📊', 'v3 trap-zone rescued by Jerry', f'{v3_abs:.1f} v3 + Jerry confirms {jerry_abs:.1f}')
            v3_trap_rescued = True
        # else: silent zero, as before
    elif v3_abs >= 1.0:
        _add(side_drivers, 8, '📊', 'v3 spread edge', f'{v3_abs:.1f}-run v3 vs market')
    elif v3_abs >= 0.5:
        _add(side_drivers, 3, '📊', 'v3 spread lean', f'{v3_abs:.1f}-run v3 vs market')

    # Jerry contribution (independent — different model architecture)
    if jerry_signed is not None:
        if jerry_abs >= 2.0:
            _add(side_drivers, 13, '🧠', 'Jerry market disagreement', f'{jerry_abs:.1f}-run Jerry vs market')
        elif jerry_abs >= 1.5:
            _add(side_drivers, 8, '🧠', 'Jerry spread edge', f'{jerry_abs:.1f}-run Jerry vs market')
        elif jerry_abs >= 1.0:
            _add(side_drivers, 5, '🧠', 'Jerry spread edge', f'{jerry_abs:.1f}-run Jerry vs market')
        elif jerry_abs >= 0.5:
            _add(side_drivers, 2, '🧠', 'Jerry spread lean', f'{jerry_abs:.1f}-run Jerry vs market')

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
        #     model signals (SD/PHI 6/3 incident). Tier caps at STRONG via
        #     the _dim_tier "PRIME requires play" rule and write_sweat_score's
        #     79-cap when no play exists. Score, drivers, direction call all
        #     remain intact for transparency.
        prop_conflict = (prop_dir is not None and prop_dir != delta_direction
                         and (prop_dir_prime + prop_dir_strong) >= 4)
        v3_jerry_conflict = (
            jerry_total_delta_pre is not None
            and abs(jerry_total_delta_pre) >= 0.5
            and total_delta_abs >= 0.5
            and ((total_delta_signed > 0) != (jerry_total_delta_pre > 0))
        )
        if prop_conflict:
            total_delta_suppressed = True
            _evidence('⚠️', 'Total delta vs props conflict',
                      f'Model {total_delta_signed:+.2f} {delta_direction}, {prop_dir_prime + prop_dir_strong} players point {prop_dir} — suppressed')
        elif v3_jerry_conflict:
            total_delta_suppressed = True
            jerry_dir_pre = 'OVER' if jerry_total_delta_pre > 0 else 'UNDER'
            _evidence('⚠️', 'v3 vs Jerry total conflict',
                      f'v3 {total_delta_signed:+.2f} {delta_direction}, Jerry {jerry_total_delta_pre:+.2f} {jerry_dir_pre} — both total signals suppressed')
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

    # ---- TOTAL: Jerry total disagreement (REWEIGHTED 2026-06-03) ----
    # 6/2 audit: Jerry total MAE 2.29 vs v3 MAE 2.71 — Jerry directionally
    # accurate 9-2 (81.8%) on the night, v3 was 4-1 (80%). Jerry now best
    # total model in production. Bumping Jerry's bands roughly to match v3
    # (was ~67% of v3) so the dim score reflects Jerry's real predictive
    # weight. OVER skepticism multiplier still applies (v4 OVER drift per
    # [[project_v4_over_drift]]).
    jerry_total = ctx.get('jerry_pred_total')
    if jerry_total is not None and close_total > 0 and not total_delta_suppressed:
        try:
            jerry_total_delta_signed = round(float(jerry_total) - close_total, 2)
            jerry_total_delta_abs = abs(jerry_total_delta_signed)
            jerry_dir = 'OVER' if jerry_total_delta_signed > 0 else 'UNDER'
            jerry_mult = 1.0
            if jerry_dir == 'OVER':
                # Reuse same v4-consensus + v4-suppressed rule as v3 OVER
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
            if jerry_total_delta_abs >= 2.5:
                pts = int(round(17 * jerry_mult))
                _add(total_drivers, pts, '🧠', 'Jerry major total disagreement', f'{jerry_total_delta_signed:+.2f}-run Jerry vs market')
            elif jerry_total_delta_abs >= 1.5:
                pts = int(round(13 * jerry_mult))
                _add(total_drivers, pts, '🧠', 'Jerry strong total disagreement', f'{jerry_total_delta_signed:+.2f}-run Jerry vs market')
            elif jerry_total_delta_abs >= 1.0:
                pts = int(round(9 * jerry_mult))
                _add(total_drivers, pts, '🧠', 'Jerry total edge', f'{jerry_total_delta_signed:+.2f}-run Jerry vs market')
            elif jerry_total_delta_abs >= 0.5:
                pts = int(round(5 * jerry_mult))
                _add(total_drivers, pts, '🧠', 'Jerry total lean', f'{jerry_total_delta_signed:+.2f}-run Jerry vs market')
        except (TypeError, ValueError):
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

    # Jerry-driven SIDE play fallback (2026-06-01). Sweat dim scorer was
    # already crediting Jerry's spread + offense drift + confluence as SIDE
    # drivers (5/31 fix), but the play resolver here only surfaced a side
    # when compute_primary_play named an ML/RL externally. That left MIA/WSH
    # SIDE 69/STRONG (Jerry +3.71 / drift gap / confluence +4) with no
    # actionable label — Sweat Card showed empty even though the math said
    # play it. 5/31 audit: Jerry ML 13-2 (86.7%), |spread|>=2.0 = 8-1
    # (88.9%) on day-1 production — strong enough evidence to surface a
    # side play when the dim itself has cleared STRONG.
    #
    # Gate: side_score >= 65 (STRONG floor) AND we can resolve a direction
    # from Jerry's signed disagreement with market (preferred) or v3's
    # signed disagreement (fallback) at >=1.0 run magnitude. Tier mirrors
    # the dim score so PRIME/STRONG/LIGHT_LEAN propagate to UI consistently.
    if side_play is None and side_score >= 65 and close_spread_val is not None:
        pick_home = None
        try:
            if jerry_signed is not None and abs(float(jerry_signed)) >= 1.0:
                pick_home = float(jerry_signed) > 0
            elif v3_signed is not None and abs(float(v3_signed)) >= 1.0:
                pick_home = float(v3_signed) > 0
        except (TypeError, ValueError):
            pick_home = None
        if pick_home is not None:
            team = ctx.get('home_team') if pick_home else ctx.get('away_team')
            if team:
                play_tier = 'PRIME' if side_score >= 80 else 'STRONG' if side_score >= 65 else 'LIGHT_LEAN'
                src = 'jerry_consensus' if jerry_signed is not None and abs(float(jerry_signed)) >= 1.0 else 'v3_consensus'
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

    # ---- Backward-compat: sort/cap legacy contributions list ----
    if track.get('contributions'):
        track['contributions'].sort(key=lambda c: -c.get('points', 0))
        track['contributions'] = track['contributions'][:6]
    if track.get('evidence'):
        track['evidence'] = track['evidence'][:5]

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
    SCORE_OVERRIDE_THRESHOLD = 20  # 20-point score delta to override locked pick
    existing_pick = None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache?game_id=eq.best_bet_{today}&select=data",
            headers=HEADERS
        )
        existing = r.json()
        if existing and len(existing) > 0 and existing[0].get('data', {}).get('pipelineGenerated'):
            existing_pick = existing[0]['data']
            existing_score = existing_pick.get('score', {}).get('total', 0) or 0

            # Manual-override lock — set when POTD is hand-picked (e.g. user
            # already posted to social before pipeline shifted). Wins against
            # all auto-regeneration paths regardless of ET hour.
            if existing_pick.get('manualOverride'):
                print(f"🔒 manualOverride=true — POTD hand-locked, skipping all regeneration")
                return

            if et_hour < 11:
                print(f"⏰ Pre-11am ET ({et_hour}h) — regenerating with fresh data")
                existing_pick = None  # clear so we overwrite
            elif et_hour < 14:
                print(f"✅ Today's pick locked (11am-2pm window) — skipping regeneration")
                return
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
    def _cohort_rate(cohort_key):
        """Pull latest 30d hit rate for the cohort from mlb_tier_calibration.
        Cached per-run. Returns (rate, n) or (None, 0) if cohort isn't
        calibrated yet."""
        if not cohort_key:
            return (None, 0)
        if cohort_key in _cohort_cache:
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
        # v2 Total OVER/UNDER edge — no calibrated cohort yet. Spread_delta_ge2
        # is the closest proxy but it audits in the low-50s, won't clear
        # MIN_AUDIT_RATE. Dropping these from POTD eligibility until a
        # dedicated cohort accumulates.
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
