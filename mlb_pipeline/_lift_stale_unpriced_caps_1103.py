"""Lift the unpriced-market cap on UNPLAYED rows the rebuild no longer touches.

== WHY A ONE-OFF IS NEEDED AT ALL ========================================
The gate's restore branch is correct -- replayed against the live rows it
turns 2026020247 and 2026020249 from COVERAGE back to LEAN and correctly
leaves 2026020255 capped, because that game still has no price. But a full
NHL context rebuild (`nhl_game_context.py --days 7`) did NOT restore them,
and the reason is structural, not a bug in the gate:

`nhl_game_context._apply_ensemble` builds `row['primary_play']` from
scratch on every rebuild, and RETURNS EARLY when the ensemble has no
opinion (`decision is None` / `top.pick is None`) or the season gate
refuses. The upsert then "preserves the published primary_play only". So a
pick the ensemble has stopped forming -- or one the publish lock has frozen
-- is never handed to the gates again, and its stale cap is permanent.

That is why the docstring promise "the tier restores itself as soon as a
price arrives" fails for exactly the rows that need it: restoring requires
the pick to be REGENERATED, and these are the picks that no longer are.

== WHAT THIS DOES ========================================================
Calls apply_unpriced_market_gate -- the real gate, not a hand-written tier
-- on every flagged row whose game has NOT started, and persists whatever
it returns. If the gate declines (still no price), nothing is written.

Settled games are excluded: re-tiering a played game rewrites what we
published after the fact, and the public record has to say what we actually
showed (feedback_fix_the_cache_not_the_frozen_receipt).

Readback-verified, and it reports loudly if the publish lock reverts a
write (feedback_204_is_not_a_write, feedback_fix_the_cache_not_the_frozen_receipt).

    python _lift_stale_unpriced_caps_1103.py            # dry run
    python _lift_stale_unpriced_caps_1103.py --apply
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

APPLY = '--apply' in sys.argv
TABLE = 'nhl_game_context'


def main() -> int:
    from defensive_gates import apply_unpriced_market_gate

    now = datetime.now(timezone.utc)
    r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H, timeout=90,
                     params={'select': '*', 'limit': 2000})
    r.raise_for_status()
    rows = r.json() or []

    plan, declined, played = [], [], 0
    for row in rows:
        pp = row.get('primary_play')
        if not isinstance(pp, dict) or not pp.get('_unpriced_market'):
            continue
        ct = row.get('commence_time')
        started = True
        if ct:
            try:
                started = datetime.fromisoformat(
                    str(ct).replace('Z', '+00:00')) <= now
            except ValueError:
                started = True
        if started:
            played += 1
            continue
        out = apply_unpriced_market_gate(dict(pp), row, sport='NHL')
        if not isinstance(out, dict) or out.get('tier') == pp.get('tier'):
            declined.append(row)
            continue
        plan.append((row, pp, out))

    print(f'{TABLE}: {len(rows)} rows')
    print(f'  flagged, game already started  : {played}  (left alone)')
    print(f'  flagged, unplayed, gate DECLINES: {len(declined)}')
    for row in declined:
        print(f"      {row['game_date']}  {row['away_team'][:18]:18} @ "
              f"{row['home_team'][:18]:18} still unpriced")
    print(f'  flagged, unplayed, gate RESTORES: {len(plan)}')
    for row, pp, out in plan:
        print(f"      {row['game_date']}  {row['away_team'][:18]:18} @ "
              f"{row['home_team'][:18]:18} {str(pp.get('type')):4} "
              f"{pp.get('tier')} -> {out.get('tier')}")
    if not APPLY:
        print('\n(dry run -- pass --apply to write)')
        return 0

    rc = 0
    print()
    for row, pp, out in plan:
        gid = row['game_id']
        pr = requests.patch(f'{SB}/rest/v1/{TABLE}', headers=H_W, timeout=30,
                            params={'game_id': f'eq.{gid}'},
                            data=json.dumps({'primary_play': out}))
        if pr.status_code not in (200, 204):
            print(f'  !! {gid} PATCH {pr.status_code}: {pr.text[:160]}')
            rc = 1
            continue
        rb = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H, timeout=30,
                          params={'select': 'primary_play',
                                  'game_id': f'eq.{gid}'})
        got = ((rb.json() or [{}])[0].get('primary_play') or {})
        if got.get('tier') != out.get('tier'):
            print(f"  !! {gid} NOT CONFIRMED -- readback tier="
                  f"{got.get('tier')!r}, wanted {out.get('tier')!r}. The "
                  f'publish lock trigger may have reverted it.')
            rc = 1
        else:
            print(f"  confirmed {gid}  tier={got.get('tier')}  "
                  f"flag={got.get('_unpriced_market')}  "
                  f"restored={bool(got.get('_unpriced_restored'))}")
    return rc


if __name__ == '__main__':
    sys.exit(main())
