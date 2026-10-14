# 8/5 LOTTO CARD — LOCKED (audit AM 8/6)

Generated: 2026-08-05 evening
Data source: mlb_game_context, mlb_pipeline_props, prop_jerry_reads @ 21:02 UTC

## 🔒 LOCK TIER (5)

| # | Pick | Line | Odds | Player Team | Conv/Refit | Projection |
|---|------|------|------|-------------|------------|------------|
| 1 | Andrew Painter ER Over | 2.5 | -115 | PHI (home) | PRIME 100 / Jerry 84 | 3.9 vs 2.5 (+56%) |
| 2 | Dean Kremer ER Over | 2.5 | -125 | MIN (away) | PRIME 98 / Jerry 82 | 3.7 vs 2.5 (+48%) |
| 3 | Jameson Taillon ER Over | 2.5 | -135 | TOR (away) | PRIME 95 / Jerry 82 | 3.6 vs 2.5 (+44%) |
| 4 | Tomoyuki Sugano BB Under | 1.5 | -130 | COL (home) | PRIME 86 / refit 98.4 / Jerry 80 | 0.7 vs 1.5 |
| 5 | Casey Mize BB Under | 1.5 | -120 | SD (away) | PRIME 82 / Jerry 67 | 1.0 vs 1.5 |

## 💪 STRONG TIER (5)

| # | Pick | Line | Odds | Player Team | Conv/Refit | Projection |
|---|------|------|------|-------------|------------|------------|
| 6 | Sean Burke KS Over | 5.5 | -142 | CWS (away) | STRONG 73 / Jerry 85 | 8.4 vs 5.5 (+52%) |
| 7 | Sean Burke BB Under | 1.5 | -125 | CWS (away) | STRONG 68 / refit 98 / Jerry 80 | 1.1 vs 1.5 |
| 8 | Trevor Rogers ER Under | 2.5 | -160 | BAL (home) | Jerry 78 | 1.3 vs 2.5 (-48%) |
| 9 | Reid Detmers BB Over | 1.5 | -130 | LAA (away) | Jerry 79 / refit 84.5 | 2.0 vs 1.5 |
| 10 | Sonny Gray BB Over | 1.5 | -130 | BOS (home) | Jerry 82 / refit 84.5 | 1.9 vs 1.5 (+27%) |

## ⚖️ MEDIUM TIER (5)

| # | Pick | Line | Odds | Player Team | Conv/Refit | Projection |
|---|------|------|------|-------------|------------|------------|
| 11 | Rhett Lowder OUTS Under | 15.5 | -101 | CIN (home) | PRIME 68 / Jerry PASS | 10.7 vs 15.5 |
| 12 | Bryce Elder BB Over | 1.5 | -120 | ATL (home) | Jerry 81 / refit 84.5 | 2.1 vs 1.5 (+40%) |
| 13 | Dean Kremer KS Over | 4.5 | -115 | MIN (away) | Jerry 67 / **refit 0.0 flag** | 5.4 vs 4.5 (+20%) |
| 14 | Shota Imanaga KS Over | 4.5 | -130 | CHC (home) | Jerry 62 / **refit 0.0 flag** | 5.0 vs 4.5 (+11%) |
| 15 | Tomoyuki Sugano KS Over | 2.5 | -120 | COL (home) | Jerry 72 / **refit 0.0 flag** | 3.1 vs 2.5 (+24%) |

## 🎰 LOTTO/BOOSTER TIER (5)

| # | Pick | Line | Odds | Notes |
|---|------|------|------|-------|
| 16 | DET ML | — | +143 | MC HIGH-CONF 84.4% AWAY, PRIME conf +5. Woo L3 6.75 ERA. |
| 17 | CHC ML | — | -108 | MC 77.7% HOME. Imanaga L3 1.62 ERA. |
| 18 | Under STL/NYY | 8.5 | — | Sharp 91% $ UNDER. Model split flag. |
| 19 | Under NYM/CLE | 7.5 | — | Sharp 91% $ UNDER. Line moved 8.5→7.5. |
| 20 | Bryce Elder HA Over | 5.5 | +100 | Jerry 60. Projection 6.4 hits. +100 fair. |

## Audit checklist AM 8/6:

- [ ] Grade each of 20 legs W/L
- [ ] Break down by tier hit rate
- [ ] Flag any refit=0.0 K OVERs that failed (validate the flag)
- [ ] Check Painter BB Under DID hit or miss (validate direction contradiction was correct call to skip)
- [ ] Verify DET Anderson outs (opener suspicion)
- [ ] Note if any batter hits_over PRIMEs we skipped hit (opportunity cost of no book_odds)

## Correlation notes:
- #1/#2/#3 = 3× "bad pitcher shelled" (independent games)
- #4/#15 = same pitcher (Sugano)
- #6/#7 = same pitcher (Burke)
- #2/#13 = same pitcher (Kremer)
- #12/#20 = same pitcher (Elder)

## Filters applied (skipped):
- HOU ML -208 (heavy-fav trap)
- Skenes/Brown/Whisenhunt/Harrison walk-prop juice traps ≥-172
- All batter hits_over PRIMEs (no book_odds)
- Painter BB Under (projection contradicts direction)
- Nick Martinez KS Under (Jerry FADE)
