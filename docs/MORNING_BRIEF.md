# Morning Brief — What to run every day

**Purpose:** eliminate the "what did last night grade / what's broken" back-and-forth every morning. Everything here should either be automated overnight OR checked in one command.

**Date convention:** "yesterday" = the ET calendar date whose games were played the day before we run this. On weekday mornings that's usually just yesterday's date; on Monday mornings that's Sunday (and we may want Sat NCAAF summarized separately).

---

## SECTION 1 — Grading completeness (are we even able to compute a record?)

For yesterday, verify these rowcounts. Each should be at/near 100% "graded" (result populated).

| Table | Filter | Expected coverage | If broken |
|---|---|---|---|
| `jerry_reads` sport=MLB | `game_date = yesterday` | 100% `result` populated | `python grade_jerry_reads.py --sport MLB` |
| `jerry_reads` sport=NCAAF | `game_date = last_saturday` | should be ≥ 80% | `python resolve_ncaaf_results.py` then `python grade_jerry_reads.py --sport NCAAF` |
| `prop_jerry_reads` sport=MLB | `game_date = yesterday` | 100% `result` populated | `python grade_prop_jerry_reads.py` |
| `mlb_pipeline_props` | `game_date = yesterday` | 100% `result` + `resolved_at` populated | `python grade_props.py` |
| `jerry_cache` game_id=`best_bet_yesterday` | one row | `data.result` populated | `python grade_potd.py --date yesterday` (built 9/8) |
| `daily_surface_records` | `date = yesterday` | one row per sport × surface | `python aggregate_daily_records.py --date yesterday` |
| `ncaaf_game_results` | `game_date = last_saturday` | one row per Sat game with score | `python resolve_ncaaf_results.py --backfill` — persistent gap, see [[project_ncaaf_grading_gap_908]] |

## SECTION 2 — Pick results

Answer for each sport:

### MLB Jerry picks
- **All Jerry reads**: W-L-P (%)
- **By tier via `mlb_game_context.primary_play.tier`**:
  - PRIME: W-L (%)
  - STRONG: W-L (%)
  - LEAN: W-L (%)
  - COVERAGE: W-L (%) (should be small; these are unpublishable filler)
- **LR-endorsed subset** (input_snapshot `_lr_tier_raw ∈ {STRONG, LEAN, MODERATE}`): W-L (%)

### MLB Props
- **All props**: W-L (%)
- **By tier** (PRIME / STRONG / LEAN / COVERAGE / SKIP): W-L (%)
- **LR-endorsed props** (signals `_lr_tier_raw ∈ {STRONG, LEAN, MODERATE}`): W-L (%)
- **refit_conviction ≥ 60**: W-L (%)

### NCAAF Jerry picks (Saturday, weekly)
Same shape as MLB Jerry — ALL / PRIME / STRONG / LEAN / LR-endorsed. Watch the "ungraded" count; that's the NCAAF grading gap surface.

### POTD (Pick of the Day)
- W or L or Push
- Sport, matchup, pick, conviction score, units-net

### Ensemble consensus (games where the ensemble was unanimous)
- Read from `mlb_game_context.confluence.signals_voted` — if all voters agreed, count as ensemble consensus
- W-L (%) among consensus picks

## SECTION 3 — Steam Room P/L (from `daily_surface_records`)

For yesterday, one line per surface, sport-aggregated:

- **Sharp** (The Sharp tab): W-L-P · units-net
- **Prop** (Props on The Sharp): W-L-P · units-net
- **Ladder** (The Ladder — single-play): W-L-P · units-net
- **Ledger** (parlays + teasers on The Ledger): W-L-P · units-net

Rolling records (from `surface_records` MTD/lifetime windows) to cross-check the daily didn't corrupt cumulative math.

## SECTION 4 — Today's pipeline health

- `mlb_pipeline_props` today: rowcount + tier distribution + `player_team = UNKNOWN` count (should be zero after 9/7 fix)
- Sharp Card cache (`jerry_cache.sharp_card_today`): item count + zero UNKNOWN team + zero graded-already leaks
- NFL prop_jerry_reads for latest game_date: rowcount + BACK% (watch the 60% PASS gap from [[project_nfl_prop_signal_gap_908]])

## SECTION 5 — Known persistent issues to check haven't regressed

Grep-check the log for repeat offenders that we've fixed:

- Peterson-style orphan pitcher props (`player_team = UNKNOWN`) — should always be zero after write-gate ships
- MoneyFlow `money_pct_avg` field vs `handle_pct_avg` (9/7 fix)
- Jerry read + call chip mismatches (9/7 jerry_pick_scrub prose-stale fix)
- Sunday NCAAF `mode='resolver_only'` skipping splits_v2 aggregator (9/7 gate-removal fix)

---

## One-shot command to run every morning

Save as `docs/scripts/morning_brief.py` (TODO — spec below), invoked as:

```
python docs/scripts/morning_brief.py --date yesterday
```

**Exit codes:**
- 0 → all green (all sports ≥80% graded, no regressions)
- 1 → grading gap warning (some sport <80% graded but no data loss)
- 2 → hard failure (missing overnight jobs, POTD ungraded, sharp card record stale)

**Auto-fire triggers:**
- End of overnight workflow (`.github/workflows/mlb_grade_overnight.yml`) — script runs, exits non-zero triggers email
- Cron 8am ET daily as backstop
- Any user tap on Steam Room "refresh" button (client-side calls `morning_health_check` RPC)

## Spec for `morning_brief.py`

```python
"""Single-command morning verification. Returns nonzero on grading gap
or missing overnight jobs. Print output structured for reading + programmatic
consumption (--json flag emits JSON for slack/email piping).

Sections in order:
1. Grading completeness (per sport, per table) — MUST be ≥80%
2. Pick results (Jerry picks by tier, LR-endorsed subset)
3. Steam Room P/L (from daily_surface_records)
4. Today's pipeline health (writes for today's slate)
5. Known regression grep (Peterson orphans, MoneyFlow field, etc.)

Usage:
    python docs/scripts/morning_brief.py                    # yesterday, human
    python docs/scripts/morning_brief.py --date 2026-09-07
    python docs/scripts/morning_brief.py --json             # for automation
    python docs/scripts/morning_brief.py --fix              # attempt auto-repair
"""
```

**Sports to include (as of 9/8):** MLB, NCAAF, NFL, and — when in-season — NBA, NHL, NCAAB, UFC. Script should read sport list from `sport_registry` (single source of truth per [[project_faq_sport_registry_source_906]]).

## The priority stack driving this brief

Per [[project_data_infrastructure_priorities_908]]:

1. **LR for all sports** — currently only MLB fires reliably. Cross-sport LR is the highest-EV lever.
2. **Grading for all sports** — MLB done, NCAAF broken (odds-pull date bug), NFL/NBA/NHL/NCAAB/UFC need audits.
3. **Daily routine automation** — this brief becoming a one-command green-check.

Until all three are solid, nothing else ships. See the memory for concrete sequencing.

---

## Related memories
- [[project_ncaaf_grading_gap_908]] — root cause of NCAAF ungraded pattern
- [[project_nfl_prop_signal_gap_908]] — NFL prop signals populate empty (60% PASS)
- [[project_website_update_queue_908]] — post-launch site parity work
