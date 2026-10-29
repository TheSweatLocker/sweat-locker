#!/usr/bin/env python3
"""Is there an MLB slate worth running the MLB-specific pipeline for?

Exit 0 = yes, there is a slate (or we cannot tell, so run anyway).
Exit 1 = no slate. The caller should SKIP, and say it skipped.

== WHY (2026-10-29) ========================================================
Andy: "with mlb not in season anymore what happens to mlb pipeline, do all
the pitcher and stat pullers still run... whats the cost?"

They do. mlb_pitcher_stats and mlb_bullpen_stats were both written on
2026-10-29 at 18:38 UTC with the last MLB game on 10-28. Measured from
start/end heartbeats over 14 days:

    mlb_pipeline          48 runs   57.5 min mean   197 min/day
    mlb_grade_overnight   42 runs   19.5 min mean    58 min/day
    ---------------------------------------------------------------
    MLB total                                      255 min/day
    ALL workflows                                  477 min/day

So MLB is 53% of every workflow minute we spend, for a sport that is not
playing. At GitHub's ~$0.008/min that is roughly $61 of $114 a month.

The money is the small half. The real cost is that 25 MLB-specific steps
will now run GREEN AND EMPTY for four months, so by spring training nobody
knows which of them still work -- feedback_green_step_that_writes_nothing,
which has already cost us 4 cases dark for days-to-weeks.

A gate that is silent is the same trap, so the caller records a SKIPPED
heartbeat rather than a success.

== WHY NOT feature_flags(MLB,'season_active') ==============================
It exists, it says `true` today, and NOTHING in the repo reads it -- grepped.
A hand-set flag is another thing to forget in March. This asks the data:
does mlb_game_context have a row for today or tomorrow ET?
"""
from __future__ import annotations
import os, sys, argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days-ahead', type=int, default=1,
                    help='also count a slate this many days ahead (default 1)')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()

    url = os.environ.get('SUPABASE_URL')
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ.get('SUPABASE_KEY'))
    if not url or not key:
        # Cannot tell -> run. A gate must never be the reason a live sport
        # goes dark (feedback_weather_pullers_silent_noop: my own
        # fail-loudly guard cost NCAAF five weeks of weather).
        print('[mlb-gate] no credentials - cannot tell, so NOT skipping')
        return 0

    et = datetime.now(timezone.utc) - timedelta(hours=4)
    today = et.date()
    dates = [(today + timedelta(days=d)).isoformat()
             for d in range(0, max(1, a.days_ahead) + 1)]
    try:
        r = requests.get(
            f'{url.rstrip("/")}/rest/v1/mlb_game_context',
            headers={'apikey': key, 'Authorization': f'Bearer {key}',
                     'Prefer': 'count=exact', 'Range': '0-0'},
            params={'select': 'game_id', 'game_date': f'in.({",".join(dates)})'},
            timeout=20)
    except Exception as e:                                     # noqa: BLE001
        print(f'[mlb-gate] probe raised {type(e).__name__} - NOT skipping: {e}')
        return 0
    if r.status_code not in (200, 206):
        print(f'[mlb-gate] probe HTTP {r.status_code} - NOT skipping')
        return 0
    try:
        n = int((r.headers.get('content-range') or '/0').split('/')[-1])
    except Exception:                                          # noqa: BLE001
        print('[mlb-gate] could not read a count - NOT skipping')
        return 0

    if n > 0:
        if not a.quiet:
            print(f'[mlb-gate] {n} MLB game(s) on {dates[0]}..{dates[-1]} - RUN')
        return 0
    print(f'[mlb-gate] no MLB games on {dates[0]}..{dates[-1]} - SKIP '
          f'(off-season / no slate)')
    return 1


if __name__ == '__main__':
    sys.exit(main())
