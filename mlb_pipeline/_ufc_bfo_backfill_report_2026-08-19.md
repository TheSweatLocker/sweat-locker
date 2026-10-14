# UFC BFO Historical Closing Odds Backfill
Run window: 2026-08-20T02:31:42.219552+00:00 → 2026-08-20T03:22:53.119413+00:00 (51.2 min)
Events processed this run: 92
Fights processed this run: 1115
Fights populated with odds: 1058 (94.9%)
Fights unmatched: 57
Fights skipped (already in JSONL): 0

## DB writes
- rows upserted to `ufc_historical_closing_odds`: 1115
- DB disabled mid-run: False
- error tallies: {}

## HTTP status distribution (BFO event pages)
- 200: 144
- none: 2

## Events with no BFO URL discovered (0)

## Events where BFO page had no parseable odds (1)
- UFC 316: Dvalishvili vs. O'Malley 2 → https://www.bestfightodds.com/events/ufc-3684

## Events where zero fights matched despite finding page (1)
- UFC 316: Dvalishvili vs. O'Malley 2 → https://www.bestfightodds.com/events/ufc-3684

## BFO 404s (0)

## BFO 429s (0)

