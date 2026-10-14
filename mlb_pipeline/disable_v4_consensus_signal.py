#!/usr/bin/env python3
"""Disable nfl_model_consensus_away_spread — a constant wearing a vote.

== 2026-10-14 - WHY ==
nfl_game_context.py:1094-1117 neutralises v4 inside that script, with a note
explaining the choice: "WHY NEUTRALISE HERE RATHER THAN AT EACH USE. Same
reason the publish lock guard lives in lock_publish: there are several
consumers and they drift. One place to re-enable when the features land." It
also records the fear: "stops it silently coming back if v4 starts writing
again while still feature-blind."

BOTH HALVES CAME TRUE. v4 started writing again -- 29 rows in September, 30 in
October -- and `signal_sources.nfl_model_consensus_away_spread` reads
`ctx.v4_spread` straight off the context row, with no such guard. The
neutralisation protects the lens count and not this signal.

MEASURED ON THE OCTOBER ROWS v4 IS WRITING NOW:
    n=30, sd 0.272, 6 distinct values, range [+0.78, +2.66]
    NEGATIVE VALUES: 0
    v4_confidence: 0.311 on every single row
    v4_features_used: away_bye, away_rest, away_short_week, div_game,
        home_bye, home_rest, home_short_week, is_dome, is_playoff, season,
        temp, week, wind
    -> not one team-strength feature. v4 cannot know who is playing.

THREE CONSEQUENCES, EACH ON ITS OWN ENOUGH TO DISABLE IT:

 1. THE CONDITION IS VACUOUS. The rule requires `float(ctx.v4_spread) > 0`,
    and v4 is never negative, so that clause is ALWAYS TRUE. The "V4 + Panel
    consensus" is panel alone with a rubber stamp. The sibling
    nfl_model_consensus_home_spread requires v4_spread < 0 and is already
    disabled -- it could never fire at all.

 2. IT MEASURES THE WRONG THING. The other clause is
    `(panel_pred_away_pts - panel_pred_home_pts) > 0`, i.e. panel thinks the
    away team wins OUTRIGHT -- not that it beats the number. It then emits
    AWAY_RL, a spread bet. On a game where the away side is favoured by 10 and
    panel predicts away by 3, this fires AWAY_RL off a correct model opinion
    and loses. A spread signal must be measured against the spread.

 3. THE DISPLAY PROSE IS FALSE. It reads "V4 + Panel models both project away
    cover". Neither half is true: v4 projected nothing about this matchup, and
    panel projected a win, not a cover. That reaches users.

It would have fired on 13 of the 27 October rows carrying all three inputs --
48% of the slate.

WHAT THIS DOES NOT DO. It does not invent a replacement. A real NFL margin
consensus has to be built from the sport's genuinely independent voices --
projected, panel and mc, which agree only 53-61% of the time (panelist_registry
.py) -- each compared to the LINE, and shadowed before it ships.
shadow_nfl_consensus.py already drafts that and must be re-pointed, because it
currently pairs mc with v3, and v3 is projected plus a rest nudge.

USAGE
    python disable_v4_consensus_signal.py --dry-run
    python disable_v4_consensus_signal.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

TARGETS = [('nfl_model_consensus_away_spread', 'NFL'),
           ('nfl_model_consensus_home_spread', 'NFL')]

REASON = ('disabled 2026-10-14: reads v4_spread, which is feature-blind and '
          'never negative (Oct n=30, sd 0.272, 0 negative, no team-strength '
          'feature), so the v4 clause is vacuous and the signal is panel '
          'alone; and its panel clause tests an OUTRIGHT win while the signal '
          'emits AWAY_RL, a spread bet. Display prose claimed two models '
          'agreed. Rebuild from projected+panel+mc vs the LINE.')


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    for key, sport in TARGETS:
        r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H,
                         params={'select': '*', 'signal_key': f'eq.{key}',
                                 'sport': f'eq.{sport}'}, timeout=120)
        rows = r.json() if r.status_code in (200, 206) else []
        if not rows:
            print(f'{key}: NOT FOUND (HTTP {r.status_code})')
            continue
        cur = rows[0]
        print(f'\n{key}  enabled={cur.get("enabled")}')
        if cur.get('enabled') is False:
            print('  already disabled — leaving it alone')
            continue
        if a.dry_run:
            print('  DRY RUN — would set enabled=false')
            continue
        # Only columns proven to exist on this row are patched: naming a
        # missing column makes PostgREST drop the WHOLE patch (PGRST204).
        patch = {'enabled': False}
        if 'disabled_reason' in cur:
            patch['disabled_reason'] = REASON
        elif 'description' in cur:
            patch['description'] = (str(cur.get('description') or '')[:400]
                                    + ' | ' + REASON)[:900]
        w = requests.patch(f'{SB}/rest/v1/signal_sources', headers=H_WRITE,
                           params={'signal_key': f'eq.{key}',
                                   'sport': f'eq.{sport}'},
                           data=json.dumps(patch), timeout=120)
        if w.status_code not in (200, 204):
            print(f'  WRITE FAILED {w.status_code}: {w.text[:250]}')
            continue
        back = w.json() if w.status_code == 200 and w.text.strip() else []
        now = back[0].get('enabled') if back else '(no body)'
        print(f'  patched -> enabled={now}')

    # Read back independently: a 204 is not proof of a write.
    print('\nverification read-back:')
    for key, sport in TARGETS:
        r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H,
                         params={'select': 'signal_key,sport,enabled',
                                 'signal_key': f'eq.{key}',
                                 'sport': f'eq.{sport}'}, timeout=120)
        rows = r.json() if r.status_code in (200, 206) else []
        for x in rows:
            print(f'  {x.get("signal_key"):<40} enabled={x.get("enabled")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
