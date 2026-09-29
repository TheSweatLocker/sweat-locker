---
name: nfl-read-enrichment-913
description: "✅ Enrichment PLUMBING shipped 2026-09-15 (commits 12152293 + e81c3a9c). NFL key_players/injuries + NCAAF efficiency/model now flow to input_snapshot. Prompts patched to REQUIRE citing them. Next cron run produces ESPN-analyst prose."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-15T20:26:17.320Z
---

## ✅ 2026-09-15 SHIPPED — root cause was whitelist drop

Not a data problem. `fetch_key_players_rolling` + `fetch_current_nfl_injuries` had been returning full data (32 teams QB1/RB1/WR1 with L3/L5/season aggregates, 367 Q/D/O designations across 32 teams) since the 9/13 Phase 2 code landed. **`build_struct` attached them to `struct`. Then `upsert_jerry_read_nfl` dropped them via a hardcoded input_snapshot whitelist.** 0/30 recent NFL reads had `key_players` in the persisted snapshot.

**Two commits, three-part fix:**

**12152293** — NFL: added `key_players`, `injuries` to the whitelist in `upsert_jerry_read_nfl` (generate_nfl_game_reads.py line 1883). Prompt `game_read_rules NFL` in prompt_templates PATCHed live (+1972 chars) with PLAYER-CITATION DISCIPLINE requiring:
- Name QB1 for both teams by full name in first 2 sentences
- Cite at least 2 rolling stats (L3/L5 yds/att/cmp_pct/YPC)
- Injury callouts for STARTER-tagged players in first paragraph
- Bans "the QB" / "the running back" generic phrases
- Gap-disclosure when key_players slot missing

**e81c3a9c** — NCAAF (and NBA/NCAAB/UFC/NHL going forward): shared `jerry_reads_dual_write.upsert_jerry_read` had a WORSE whitelist (only `{source, matchup}`). Expanded to include market/model/efficiency/confluence/primary_play/sweat/cohort_tags/pre_parsed_facts/casual_summary/key_players/injuries/team_snapshot/signals/align_status/season/week. Prompt `game_read_rules NCAAF` PATCHed live (+1634 chars) with TEAM-EFFICIENCY DISCIPLINE requiring:
- Cite 2+ EPA/SP+ numbers from struct.efficiency or struct.model
- Name signal_confluence.breakdown drivers when present
- Bans "efficient offense" / "stout defense" without numbers
- Gap-disclosure fallback for FCS/thin-data games

**Rollout:** next `nfl_pipeline.yml` / `ncaaf_pipeline.yml` cron persists enriched snapshots. Manual `--force` invocation lands it immediately (Claude cost). To validate: post-run, `SELECT jsonb_object_keys(input_snapshot) FROM jerry_reads WHERE sport='NFL' AND generated_at > NOW() - INTERVAL '1 hour'` should include `key_players` + `injuries`. Prose should name QB1s by full name.

Original 2026-09-13 spec below (data plumbing sections still accurate; phases 1-4 were largely already implemented, phase 5 NCAAF now inherits from the same shared write path).

---


**Andy directive 2026-09-13**: NFL reads need to read like an ESPN NFL analyst. Same treatment as MLB reads. Jerry references a curated catalog of key players + rolling stats so prose is grounded, not hallucinated. **The moat: we run the numbers so the user doesn't have to.** Target: revamp NFL process by Wed 9/17.

## Why (the moat framing)

Current NFL reads read generic ("home team has been playing well", "the offense is efficient"). MLB reads name specific pitchers, cite specific L3/L5 stats, and give the user something they couldn't easily assemble themselves. NFL is behind. Casual bettors don't do the research — that IS the product.

## Failure modes to prevent

- Hallucinated player names ("Frank Thomas" as Panthers coach class of bug — fixed for POTD prompt but read composer needs same treatment)
- Generic language ("efficient offense", "solid defense") without a number attached
- Referring to games/players the input_snapshot doesn't verify
- Cross-sport vocab bleed (fixed in 62efcf41 but re-verify)

## input_snapshot spec (mirrors MLB pattern)

Each NFL game read pulls a **structured JSON snapshot** the LLM is instructed to cite verbatim. Grounded prose only — Jerry can only use numbers/names that appear in the snapshot.

### 1. Key players roster (per team)
Populated from `nfl_depth_charts` + `nfl_injuries_espn_pull`:
- **Offense:** QB1 (name, throws, jersey), backup QB (if starter Q/D), RB1 (attempts leader last 3g), WR1, WR2 or TE1
- **Defense:** top pass rusher (season sacks), top corner (targets against), leading tackler
- **Injury OUT / QUESTIONABLE** designations for any of the above

### 2. QB rolling stats (L3, L5, season)
- Cmp% · YPA · Pass Yds/gm · TD/INT · Sacks taken · Rush Yds
- vs this defense: career QB rating + last-3 game QB rating (already available in `nfl_game_context.home_qb_vs_team_*` columns — surface!)

### 3. RB1 rolling stats (L3, L5)
- Attempts/gm · YPC · Rush Yds/gm · Rec targets · TD

### 4. WR1 / TE1 rolling stats (L3, L5)
- Targets/gm · Receptions/gm · Yds/gm · Air yards · TD

### 5. Team offense rolling (L3, L5, season)
- PPG · YPG · 3rd-down conv % · Red zone TD % · Turnovers/gm · Penalties/gm (already surfaced via `matched_patterns`)
- **Pace:** plays/gm · seconds/play · time of possession

### 6. Team defense rolling (L3, L5, season)
- PPG allowed · YPG allowed · Sacks/gm · INTs · Pressure %
- **vs pass:** yds/att allowed · TD% allowed
- **vs run:** yds/att allowed · rushing TDs allowed

### 7. Matchup-specific edges
- **QB vs DEF:** starter's rating against this opponent (career + recent) — cols already exist
- **RB vs run DEF:** where does opponent rank vs run this season?
- **WR1 vs top CB:** shadow scenario + separation trend
- **Division / conference / H2H:** last 5 head-to-head — already in `home_ats_last4` + surface it

### 8. Context signals
- **Weather:** temp / wind mph / precip probability (nfl_weather_pull already exists)
- **Rest:** days since last game (short week: <7 = flag)
- **Travel:** time zones crossed
- **Motivation:** division game / must-win / revenge (playoff context Nov+, ignore early season)

### 9. Market anchors
- Spread open → current (movement direction + magnitude)
- Total open → current
- Sharp money % (bets vs handle imbalance) — already in align_status.chips_extra
- Public bet % on each side

### 10. Model outputs (surfaced explicitly)
- projected_spread + market_spread + delta
- pred_home_points + pred_away_points + total pred
- v3/v4 ensemble votes + LR shadow probability
- Anchor status (raw vs anchored + weight)

## Prose composer rules (Jerry's guardrails)

Modeled on the `build_football_prompt` from `generate_potd_narrative.py` (commit b0ddf800 — the Frank Thomas kill):

1. **Rule 1 — Only listed team/player names.** No generic "the QB" / "their offense" / "the defense." Every proper noun must appear in input_snapshot.
2. **Rule 2 — Only listed stats.** Every number in the read must appear verbatim in the snapshot.
3. **Rule 3 — Ban tout language.** "printing money", "lock of the day", "hammer", "smash", "money in the bank" — hard ban.
4. **Rule 4 — Argue FOR the pick side.** No both-sides prose. Take a side and defend it.
5. **Rule 5 — Cite the source.** Every claim ends with a stat reference (parenthetical or clause). "Jets scored 27 (24.5 season PPG, +2.5 vs opponent 22 PPG allowed) …"
6. **Rule 6 — Weekly context caveat.** Week 1-3: append "Week N sample size — treat with early-season caution." Adjust threshold per week count.
7. **Rule 7 — Injury-aware.** If QB1 is OUT / Q, that goes in the lede, not buried.

## Implementation phases

**Phase 1 — data plumbing (Mon 9/15 - Tue 9/16):**
- Audit `nfl_depth_charts` freshness — is it populated? If not, add `nfl_depth_charts_pull.py`
- Verify `nfl_player_stats` L3/L5/season aggregation exists per player (like `mlb_pitcher_stats`)
- If missing: build `nfl_player_rolling.py` cron that materializes weekly
- Verify all input_snapshot fields have a data source

**Phase 2 — input_snapshot builder (Tue 9/16):**
- New module `mlb_pipeline/nfl_read_snapshot_builder.py`
- Per game, assemble the 10-section JSON above
- Test against 2-3 games manually — verify no NULLs on critical fields

**Phase 3 — prose composer (Wed 9/17):**
- Extend `generate_nfl_game_reads.py` (or the equivalent) to consume input_snapshot
- Port `build_football_prompt` from potd_narrative style
- Apply strict cite-only prose rules
- Add validation pass: post-generation, scan the read for any player name / team name / stat not present in input_snapshot → flag as hallucination + regenerate

**Phase 4 — QA + rollout (Wed 9/17 evening):**
- Generate reads for full Sun 9/20 NFL slate
- Manual review of 5 reads for tone + accuracy
- Compare against a control (current NFL read) side-by-side
- Approve → cron replaces the current NFL read generator

**Phase 5 — NCAAF Top-25 extension (post-9/17):**
- Same treatment, scoped to Top-25 (or AP-ranked) matchups only
- FCS + G5 stays with lighter treatment (less data available anyway)
- Reuses input_snapshot builder with NCAAF data sources

## Comparison anchor: MLB does this well because…

- `jerry_reads.input_snapshot` has ~5-9 populated sections per game
- Composer references starting pitchers by name + L5 xERA + L3 K/9
- Cites specific stat lines: "Skenes has struck out 8+ in each of his last 3"
- Names specific batters + splits vs the pitcher
- Weather + park + bullpen usage all in the mix

NFL/NCAAF needs to match. Right now reads say "home team is strong at home" — MLB says "Snell is 4-0 with 0.87 ERA at home this year (34.2 IP, 41 K)."

## Success metric

After rollout: manually read 5 randomly-selected NFL reads. Every one should
- Name QB1 for both teams by full name
- Cite 2+ specific numbers with sources
- Note at least one matchup edge (or explicit "no edge visible")
- Have zero generic phrases like "efficient offense" without a number attached
- Have zero hallucinated names (validation pass catches this)

## Files this touches (rough scope)

- `mlb_pipeline/nfl_depth_charts_pull.py` (NEW — if missing)
- `mlb_pipeline/nfl_player_rolling.py` (NEW — L3/L5/season aggregator)
- `mlb_pipeline/nfl_read_snapshot_builder.py` (NEW — input_snapshot assembler)
- `mlb_pipeline/generate_nfl_game_reads.py` (extend to consume snapshot + strict prompt)
- `mlb_pipeline/nfl_read_validator.py` (NEW — hallucination checker)
- `.github/workflows/nfl_pipeline.yml` (wire the new modules)

## Related

- [[project_nfl_prop_signal_gap_908]] — different problem (prop signals empty), but same "NFL needs the MLB treatment" theme
- [[project_nfl_prop_jerry_needs_work_906]] — Prop Jerry read gap
- [[feedback_never_generic_pitcher_ref_809]] — same rule class (never generic references)
- [[project_technical_reference_manual_906]] — this becomes a section
