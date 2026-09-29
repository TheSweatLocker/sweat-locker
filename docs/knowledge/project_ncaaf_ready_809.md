---
name: ncaaf-ready-809
description: "NCAAF Aug 22 launch shipped 8/9: 5 bug fixes + matchup-adjusted total + sharp-fade rules + returning production + audit trail. 76 games loaded."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-08-09T19:27:35.224Z
---

**2026-08-09**: NCAAF Aug 22 launch fully ready.

**Data health**:
- `ncaaf_game_results`: 76 games loaded for 2026 (Aug 29 → Oct 17) via odds pull
- `ncaaf_team_stats`: 538 rows across 2022-2025 (2026 auto-fills post first slate)
- `ncaaf_team_aliases`: 134/134 FBS ✓
- Historical results: 15,147 games for cohort backtest
- `ncaaf_game_context`: empty until first Tue cron populates (Aug 18)

**Models Jerry sees**:
1. **SP+ (primary)** — Bill Connelly composite via CFBD (`/ratings/sp`). Already pulled by `ncaaf_stats_pull.py`. Used for `projected_spread` (sp_gap × K_PTS_SP + HFA).
2. **EPA (fallback + confluence)** — off/def EPA per play from CFBD, used when SP+ missing + as a secondary confluence signal.
3. **Matchup-adjusted total** (NEW 8/9) — uses SP+ off/def to project per-team points instead of flat 52.0 base. Formula: `LEAGUE_AVG + (sp_off_home + sp_def_away)/2`, blended 80/20 with anchor to prevent extremes.
4. **Returning production %** (NEW 8/9) — CFBD `/player/returning` → % of prior-season PPA returning. Attached as columns to game_context. Critical Weeks 1-3 variance-reduction signal.

**NCAAF-specific enhancements**:
- **`nfl_heavy_home_dog` cohort PRIME override** — 63.1% audit hit (n=65) → auto-PRIME spread
- **Brand-name-fav sharp fade rule** (`SHARP_ON_BRAND_NAME_FAV`) — sharp on Alabama/Ohio State/etc at -300+ = public trap
- **Heavier fav threshold** — SHARP_ON_HEAVY_HOME_FAV fires at -400 (NFL uses -300, NCAAF has bigger favs)

**Sharp-fade infrastructure** (`ncaaf_sharp_fade_rules.py`):
- 7 rules total (MODELS_OPPOSE_SHARP × 3, ROAD_TEAM, AWAY_FAV, LIGHT_JUICE, OPPOSES_CONFLUENCE, HEAVY_HOME_FAV, BRAND_NAME_FAV)
- All LOG mode initially — auto-activate as sample accumulates via `sharp_fade_audit_trail`
- Same cap policy as MLB/NFL (1 ACTIVE → LEAN, 2+ → READ)

**Cron flow (`.github/workflows/ncaaf_pipeline.yml`)**:
1. Odds pull → `ncaaf_game_results`
2. Tue: CFBD team stats + returning production pull (NEW)
3. Build ncaaf_game_context (projections + SP+ + EPA + returning prod)
4. Generate NCAAF game reads (Jerry synthesis)
5. External aggregation
6. Resolve completed games
7. Cohort backfill
8. NEW: sharp_fade_audit write-today --sport NCAAF
9. NEW: sharp_fade_audit backfill-results

**5 critical bugs fixed 8/9** (from earlier subagent audit):
- Spread convention flip — removed
- Game ID mismatch resolver — dual-key lookup added
- Duplicate dict key in generate_ncaaf_game_reads — fixed
- Alias schema mismatch in pull_externals_ncaaf — fixed
- Timezone DST bug — switched to ZoneInfo

**Known limitations at ship**:
- SP+ preseason ratings blank until Connelly drops them (~mid-August each year); pipeline auto-populates on next pull
- Returning production is OFFENSE ONLY (CFBD doesn't split defense) — using offense as proxy
- Sharp-fade rules all LOG mode initially (activate as sample builds ~4-6 weeks)
- Neutral-site opening weekend games can have home/away swapped by Odds API — flag any Week 1 opener manually before publishing
- Cohort_backfill still uses timezone fix from 8/9 (ZoneInfo/America/New_York)

**Scope per project_ncaaf_scope**: v1.0 = Spread/Total/ML only. NO props (college prop markets thin + no fantasy projection source).

**File map**:
- `mlb_pipeline/ncaaf_game_context.py` — projection compute + primary_play resolver (SP+ matchup total + returning prod)
- `mlb_pipeline/ncaaf_sharp_fade_rules.py` — 7 sharp-fade rules (NCAAF-specific)
- `mlb_pipeline/ncaaf_returning_production_pull.py` — CFBD /player/returning puller
- `mlb_pipeline/ncaaf_stats_pull.py` — CFBD /ratings/sp + advanced stats (existed)
- `supabase/migrations/20260809_ncaaf_sp_plus_columns.sql` — new cols + tables
- `sharp_fade_audit_trail` — same table as MLB/NFL, sport-agnostic

Related: [[project_ncaaf_scope]], [[project_ncaaf_phase1_complete_723]], [[project_sharp_money_fade_808]].
