"""prop_snapshot_overlay — one helper, every composer uses it.

Problem 2026-09-09: Sharp Card, Sweat Card top_8, Dawg, Daily Degen,
Ledger, Ladder all read from live mlb_pipeline_props. Tiers + odds drift
during the day as calibration + refit re-runs. Same play appears with
different tiers in different cache rows (e.g., WAS/SD Under 8.0 was
PRIME on Sharp Card items list but LEAN on Sweat Card top_8 on 9/8).

Fix (this module): every composer that reads props calls
`overlay_from_snapshots(props, game_date)`. The helper fetches
prop_pick_snapshots for the day (source='card_lock' — locked first-write
per day via ignore-duplicates) and overlays snapshot values onto the
matching live props. Snapshot wins on tier + conviction + book_odds +
prop_line. Fields the snapshot doesn't touch (signals, player_l10_hit_*,
matchup, etc.) are left as-is on the live row.

Falls through gracefully:
- Snapshot table missing → returns props unchanged
- No snapshot rows for the date → returns props unchanged
- Partial coverage (only some snapshots written) → only matching props
  get overlaid, others stay on live values

Requires snapshot_pick_lock to fire BEFORE the composer (workflow order
enforced 2026-09-09 · commit e8fbe17e — snapshot step moved above
generate_sharp_card).

USAGE:
    from prop_snapshot_overlay import overlay_from_snapshots
    props = overlay_from_snapshots(props, game_date, sport='MLB')

Idempotent + no writes — pure read + in-place mutation of caller's list.
"""
from __future__ import annotations
import os
import requests

_SB = os.environ.get('SUPABASE_URL')
_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY')
_H = {'apikey': _KEY, 'Authorization': f'Bearer {_KEY}'} if _KEY else None

# 2026-09-09 v2: SNAPSHOT LOCKS ODDS ONLY, NOT TIER.
# Rationale: snapshot fires at ~11am ET to lock accountability odds so
# grader PnL uses the price users actually saw. But snapshot captures
# whatever tier existed at snapshot time — and tier calibration runs
# LATER in the day (refit + apply_calibration + playbook). If we overlay
# tier from snapshot, we DOWNGRADE PRIMEs back to LEAN because calibration
# hadn't run yet at snapshot time (verified 9/9: all 16 live PRIMEs were
# snapshotted as LEAN conv=55 default). Composers then displayed STRONG
# props instead of the actual PRIMEs.
# Fix: overlay only book_line + book_over_odds + book_under_odds. Tier
# and conviction stay on the live row (post-calibration truth).
_LOCK_FIELDS = {
    'book_line':            'book_line',
    'book_over_odds':       'book_over_odds',
    'book_under_odds':      'book_under_odds',
}


def overlay_from_snapshots(props: list, game_date: str,
                            sport: str = 'MLB',
                            snapshot_source: str = 'card_lock',
                            verbose: bool = True) -> list:
    """Overlay prop_pick_snapshots values on live prop rows.

    Mutates + returns the input list. Match key is
    (player_name, prop_type, direction, prop_line).

    Args:
        props: list of dicts from mlb_pipeline_props query
        game_date: 'YYYY-MM-DD'
        sport: 'MLB' default; other sports don't have snapshots today
        snapshot_source: 'card_lock' (default) | 'morning' | 'afternoon'
        verbose: print overlay count when > 0

    Returns:
        Same list, mutated in place. Empty in → empty out.
    """
    if not props or not _SB or not _H:
        return props

    try:
        r = requests.get(
            f'{_SB}/rest/v1/prop_pick_snapshots',
            headers=_H,
            params={
                'select': 'player_name,prop_type,direction,prop_line,'
                          'legacy_tier,legacy_conviction,refit_conviction,'
                          'book_line,book_over_odds,book_under_odds',
                'game_date': f'eq.{game_date}',
                'snapshot_source': f'eq.{snapshot_source}',
                'sport': f'eq.{sport}',
            },
            timeout=8,
        )
        if r.status_code != 200: return props
        snaps = r.json()
        if not isinstance(snaps, list) or not snaps: return props
    except Exception:
        return props

    by_key = {
        (s.get('player_name'), s.get('prop_type'),
         s.get('direction'), s.get('prop_line')): s
        for s in snaps
    }
    overlaid = 0
    for p in props:
        k = (p.get('player_name'), p.get('prop_type'),
             p.get('direction'), p.get('prop_line'))
        s = by_key.get(k)
        if not s: continue
        for src_col, dst_col in _LOCK_FIELDS.items():
            v = s.get(src_col)
            if v is not None:
                p[dst_col] = v
        overlaid += 1

    if verbose and overlaid:
        # ASCII marker (Windows cp1252 chokes on emoji when a run pipes output)
        print(f'  [LOCK] snapshot overlay: {overlaid}/{len(props)} props locked')

    return props
