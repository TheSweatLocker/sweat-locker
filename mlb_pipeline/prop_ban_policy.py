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


# Sanity self-test — smoke-check policy from CLI
if __name__ == '__main__':
    tests = [
        # (prop_type, tier, expected_banned, description)
        ('hits_over',       'PRIME',    False, 'hits_over — should NOT be banned'),
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
