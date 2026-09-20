"""Shared MLB prop-family ban policy — single source of truth.

Andy 9/17: five composer files each had their own copy of the batter-
family ban list. When I un-banned server-side without a v1.0.1 client,
the drift compounded because POTD composer had never had the filter at
all. This module centralizes the policy so every surface reads the same
list.

Every composer that reads mlb_pipeline_props (or its downstream
prop_jerry_reads / v_mlb_props_publishable) MUST filter through
`is_banned_mlb_prop()` before emitting to a user-facing surface.

Current policy (2026-09-17 EMERGENCY RESTORE):
  - Full ban on all 8 batter-family variants (both over + under)
    for hr / rbis / total_bases / runs
  - Full ban on batter_ks (both sides)

Reactivation flow (post v1.0.1 launch verified):
  - Set _MLB_ALLOW_STRONG_UNDER = True to un-ban STRONG+ UNDER variants
    of hr / rbis / total_bases (NOT runs — retro math doesn't clear
    market juice — see project_lr_under_family_unban_913 analysis).
  - Reapply migration 20260916h at the view level.
  - Restart consumers so this module reloads its config.

Consumers (verified as of 2026-09-17):
  generate_prop_jerry_synthesis.py    — synth-level filter
  generate_sweat_card.py              — fetch_top_props
  generate_sharp_card.py              — mlb_props fetch
  generate_daily_degen.py             — fetch_pipeline_props
  jerry_anchor_potd.py                — POTD candidate loop
"""
from __future__ import annotations
import os


# Permanent OVER ban — 30d retro hit rates were disasters (12-46%).
# These stay banned regardless of client label readiness or tier gate.
_MLB_PERMANENT_OVER_BAN = frozenset({
    'rbis_over',
    'total_bases_over',
    'hr_over',
    'runs_over',
})

# batter_ks both sides — never had a dedicated model + tiny sample
# (2026-08 audit: n=4 batter_ks_over 0-4, batter_ks_under n=5 5-0
# with only 5 opportunities). Not enough signal to publish either side.
_MLB_BATTER_KS_BAN = frozenset({
    'batter_ks_over',
    'batter_ks_under',
})

# UNDER-family gate: currently BANNED because v1.0.1 client is not live
# yet (raw uppercase "TOTAL_BASES" tab chips leaked to TestFlight when
# server un-banned before client shipped labels — Andy 9/17 AM audit).
#
# To reactivate post-v1.0.1:
#   1. Verify client build has PROP_TYPE_LABELS entries for hr / rbis /
#      total_bases (runs deliberately left out per juice math)
#   2. Verify label formatters for hr_under / rbis_under / total_bases_under
#   3. Set MLB_ALLOW_STRONG_UNDER=true env var OR flip constant to True
#   4. Reapply supabase migration 20260916h at the view level
#   5. Start with 0.25u pilot for 2 weeks; expand only if real book-odds
#      ROI stays positive (see [[project_lr_under_family_unban_913]]
#      recommendation post-2026-09-17).
_MLB_UNDER_FAMILY_GATE = frozenset({
    'hr_under',
    'rbis_under',
    'total_bases_under',
    # runs_under NOT in this set — retro 60.5% hit at market juice
    # (-300 to -500 typical) is BELOW breakeven. Permanent ban.
    'runs_under',
})

# Flip via env var so operators can enable without a code change +
# rollback. Default = False (banned) until v1.0.1 verified live.
_ALLOW_STRONG_UNDER = os.environ.get(
    'MLB_ALLOW_STRONG_UNDER', 'false'
).strip().lower() in ('1', 'true', 'yes', 'on')


# ── 2026-09-19 HITS: sync the Python policy with the SQL view ───────────
# v_mlb_props_publishable has banned these since 20260917b and restates
# them in 20260918c:
#   Rule 4 — hits_over  full ban (any tier)
#   Rule 6 — hits_under banned at LEAN
# This module never learned about either, so the Python policy and the
# view disagreed about what is publishable.
#
# Cost of the drift, measured on 2026-09-19: prop_jerry_reads wrote 404
# MLB reads, and 306 of them (76%) were hits_over/hits_under — props the
# view excludes, so not one could ever reach a user. 306 wasted LLM calls
# in a single day, roughly 9k/month, on rows that are filtered out one
# step later.
#
# The verdict behind the ban is in project_hits_ban_verdict_917: the
# headline 100%/73% PRIME records for hits are artifacts of bench-player
# and trap-juice concentration, not durable edge. Keeping the generation
# but paying for the narrative is the worst of both.
_MLB_HITS_OVER_BAN = frozenset({'hits_over'})
_MLB_HITS_UNDER_LEAN_BAN = frozenset({'hits_under'})


def is_banned_mlb_prop(prop_type: str | None, tier: str | None = None) -> bool:
    """Return True if this MLB prop should NOT be published to a
    user-facing surface.

    Args:
        prop_type: mlb_pipeline_props.prop_type (e.g. 'rbis_under').
        tier: mlb_pipeline_props.tier (e.g. 'PRIME', 'STRONG', 'LEAN').
              Only used when the UNDER-family gate is open — banned
              families stay banned regardless of tier.

    Every consumer should call this before emitting a prop to a surface.
    """
    if not prop_type:
        return False
    pt = prop_type.strip().lower()

    # Permanent bans — no tier can rescue these.
    if pt in _MLB_PERMANENT_OVER_BAN:
        return True
    if pt in _MLB_BATTER_KS_BAN:
        return True

    # runs_under permanent ban (retro doesn't clear market juice).
    if pt == 'runs_under':
        return True

    # hits — mirrors v_mlb_props_publishable Rules 4 and 6 exactly.
    # Keep these two checks in step with the view; they disagreed from
    # 09-17 to 09-19 and 76% of a day's prop-LLM spend went to rows the
    # view then dropped.
    if pt in _MLB_HITS_OVER_BAN:
        return True
    if pt in _MLB_HITS_UNDER_LEAN_BAN:
        if (tier or '').strip().upper() == 'LEAN':
            return True

    # 3 UNDER families gated by client readiness + tier.
    if pt in _MLB_UNDER_FAMILY_GATE and pt != 'runs_under':
        if not _ALLOW_STRONG_UNDER:
            return True   # client not ready → full ban
        # Client ready — allow only STRONG+ tier (LEAN volume floods
        # the render loop, root cause of 9/15 morning re-ban).
        t = (tier or '').strip().upper()
        return t not in ('STRONG', 'PRIME', 'ELITE')

    return False


def filter_mlb_props(props: list[dict]) -> tuple[list, int]:
    """Convenience wrapper — filters a list of prop dicts and returns
    (kept, dropped_count). Use in composers that pull mlb_pipeline_props
    directly. Reads both 'prop_type' and 'tier' fields per row.
    """
    kept = []
    for p in props:
        if is_banned_mlb_prop(p.get('prop_type'), p.get('tier')):
            continue
        kept.append(p)
    return kept, len(props) - len(kept)


# ── 2026-09-19 SPORT-AWARE DISPATCH ─────────────────────────────────────
# This module was MLB-only in both name and API, so NBA and NFL props had
# no ban hook at all — nothing could express "do not publish this family"
# outside MLB. NBA opens 2026-10-21 and its props already generate and
# score, so the hook has to exist before the season, not after a bad week.
#
# Deliberately, NBA ships with an EMPTY family ban list. There is no NBA
# prop performance data yet — inventing a ban list from intuition is the
# same mistake as the batter-family un-ban that had to be emergency-
# restored on 09-17. The framework exists; the policy gets written from
# retro hit rates once there is a real sample (see the note below).
#
# NFL likewise carries no family ban: today's Prop Jerry crash was a
# VOLUME problem (389 rows in one view), fixed with a row cap in
# 20260919a, not a family problem.
_BANS_BY_SPORT: dict[str, frozenset] = {
    'NBA': frozenset(),
    'NFL': frozenset(),
}


def is_banned_prop(sport: str | None, prop_type: str | None,
                   tier: str | None = None) -> bool:
    """Sport-aware ban check. Use this in any cross-sport composer.

    MLB keeps its full policy (permanent OVER bans, batter_ks, the
    UNDER-family client gate). Other sports check their registry entry.

    An UNKNOWN sport returns False rather than True: a default-deny here
    would silently blank every surface for a sport someone just wired up,
    which is the failure mode that hid for six days on 09-13. Adding a
    sport to _BANS_BY_SPORT is the deliberate act.
    """
    s = (sport or '').strip().upper()
    if s in ('MLB', ''):
        return is_banned_mlb_prop(prop_type, tier)
    banned = _BANS_BY_SPORT.get(s)
    if not banned or not prop_type:
        return False
    return prop_type.strip().lower() in banned


def filter_props(sport: str | None, props: list[dict]) -> tuple[list, int]:
    """Sport-aware sibling of filter_mlb_props."""
    kept = [p for p in props
            if not is_banned_prop(sport, p.get('prop_type'), p.get('tier'))]
    return kept, len(props) - len(kept)


# NBA PRE-SEASON TODO (before 2026-10-21, or within 2 weeks of tip):
#   Run the same retro that produced the MLB policy — per prop_type,
#   30d hit rate at each tier, with n. Ban any family whose OVER or
#   UNDER side cannot clear its own juice on n>=30. Until that audit
#   exists, NBA props should stay on internal surfaces only; publishing
#   an unmeasured family is exactly what the MLB ban list is an apology
#   for.


# Sanity self-test — smoke-check policy from CLI
if __name__ == '__main__':
    tests = [
        # (prop_type, tier, expected_banned, description)
        # 2026-09-19: flipped from False. This case asserted hits_over was
        # publishable while v_mlb_props_publishable had banned it outright
        # since 20260917b — the test was encoding the drift, so it passed
        # green the whole time the two layers disagreed.
        ('hits_over',       'PRIME',    True,  'hits_over — full ban (view Rule 4)'),
        ('hits_under',      'LEAN',     True,  'hits_under — LEAN ban (view Rule 6)'),
        ('hits_under',      'PRIME',    False, 'hits_under — allowed above LEAN'),
        ('rbis_over',       'PRIME',    True,  'rbis_over — permanent ban'),
        ('hr_over',         'STRONG',   True,  'hr_over — permanent ban'),
        ('runs_under',      'PRIME',    True,  'runs_under — permanent ban (juice math)'),
        ('batter_ks_over',  'PRIME',    True,  'batter_ks_over — no model, banned'),
        ('rbis_under',      'STRONG',   True,  'rbis_under STRONG — currently gated (client not live)'),
        ('total_bases_under','LEAN',    True,  'total_bases_under LEAN — always banned'),
        ('hr_under',        'PRIME',    True,  'hr_under PRIME — currently gated'),
        ('outs_over',       'STRONG',   False, 'outs_over — not batter family'),
        ('ha_under',        'LEAN',     False, 'ha_under — pitcher, not batter family'),
        ('',                'PRIME',    False, 'empty prop_type — pass through'),
        (None,              'PRIME',    False, 'None prop_type — pass through'),
    ]
    ok = 0; fail = 0
    for pt, tier, want, desc in tests:
        got = is_banned_mlb_prop(pt, tier)
        status = 'OK' if got == want else 'FAIL'
        if got == want: ok += 1
        else: fail += 1
        print(f'  [{status}] {desc}')
        print(f'          prop_type={pt!r} tier={tier!r}  want={want}  got={got}')
    print(f'\n{ok} pass · {fail} fail')
    print(f'\nCurrent runtime state: MLB_ALLOW_STRONG_UNDER = {_ALLOW_STRONG_UNDER}')
    print(f'  (set env var MLB_ALLOW_STRONG_UNDER=true to un-ban STRONG+ UNDER-family after client v1.0.1 verified)')
