# FCS Coverage Plan — 2026-09-03

## Problem

Users see NCAAF games where the away team (typically an FCS opponent) has:
- Missing Situational records
- Missing yds/game, 3rd down %, EPA, etc.
- Missing returning production
- Missing SP+ / efficiency

Example: **West Georgia Wolves @ Kennesaw State** — West Georgia (D-II→FCS)
had no situational stats populated. User asked "why?" and whether it
will start populating this season.

## Root cause

Our NCAAF stats pipeline (`pull_externals_ncaaf.py`, cohort backfills,
returning production pull) hits **College Football Data (CFBD) API** which
is **FBS-primary**. CFBD's FCS coverage is:
- Partial: some FCS teams have basic W-L / spread history
- Missing: EPA, SP+, returning production for most FCS teams
- Zero: for D-II teams like West Georgia (2026 is their first FCS year)

## Data audit (weekend of 9/5-9/7)

```
Total NCAAF games: 97
Likely FBS-vs-FCS (|spread| >= 20): 65
Missing away_sp_overall:            40 / 65   (61.5%)
Missing away_off_epa_pp:            40 / 65   (61.5%)
Missing away_returning_production:  40 / 65   (61.5%)
Missing home_* stats:                0 / 65   (0.0%)   ← 100% FBS home
```

Every one of these gaps is a real FCS/D-II opponent whose data CFBD
doesn't publish.

## Impact on picks

Two failure modes:
1. **Silence**: ensemble scorer has no away-team signals → picks are
   thin (1-2 signals total) → LEAN by default → user sees vague writeups
   (West Georgia case).
2. **Wrong-side confidence**: partial data + priors cause the FBS side to
   look too strong → ML at trap juice → COVERAGE gate demotes correctly
   but the CARD still surfaces the low-quality pick as "here's our take".

Both are already partially mitigated by:
- Signal-quality gate (LEAN requires n≥15 or VALIDATED tier) — shipped `a5b36780`
- LOW CONVICTION chip on COVERAGE tier — shipped `81849c59`
- Juice reroute to spread/total on juiced ML — shipped `2a1311d4`

## Options (ranked by effort × value)

### 1. ACCEPT AS-IS (zero effort)
FCS games render as LEAN/COVERAGE with LOW CONVICTION chip. Users see
honest "not enough data" signal on those games. **Current default**.
Downside: writeups vague, no distinctive read on FBS-vs-FCS games.

### 2. FBS-vs-FCS CHALK MODEL (medium effort — 1 day)
Historical FBS-vs-FCS pattern is well-established: **FBS teams cover
80%+ of ATS lines when spread ≤ -14 and it's a Week 1-3 matchup.**
Build a lightweight rule `ncaaf_fbs_vs_fcs_early_season` that:
- Fires FADE-away-RL when away team has NULL stats AND spread ≤ -20 AND
  game is in first 3 weeks of season
- Not blanket — only when the FCS team has zero data (unknown opponent)
- Weight: 0.35 (medium), tier: DISCOVERY (has real backtest support)

Impact: gives FBS-vs-FCS games a real supporting signal instead of
default LEAN silence.

### 3. FCS SP+ PULL (high effort — 2-3 days)
CFBD has an **FCS SP+ endpoint** (separate from FBS SP+). Wire a
weekly puller that fetches FCS SP+ ratings → `ncaaf_team_stats.sp_plus`
for FCS teams. Then ensemble scorer can use SP+ gap even for FCS games.
Downside: SP+ for FCS is noisier + updates less frequently than FBS.

### 4. D-II BLIND FADE (small effort — but blunt)
For teams like West Georgia making the FCS jump this year, FADE their
side unconditionally in Week 1-4. This is what Vegas does. But blunt
rule → maintenance risk.

## Recommendation

**Ship #2 now** (FBS-vs-FCS chalk model — 1 day, real signal for 40+
games this weekend alone). **Queue #3 for post-launch** (FCS SP+ pull
requires season-long data validation before we trust it).

## Implementation of #2

```python
# In ensemble_scorer or as a new signal in signal_registry:
def ncaaf_fbs_vs_fcs_early_season(ctx):
    """FADE away RL when FBS team is heavy home favorite over unknown-
    data opponent in first 3 weeks."""
    away_sp = ctx.get('away_sp_overall')
    home_sp = ctx.get('home_sp_overall')
    spread  = ctx.get('close_spread')
    game_dt = ctx.get('game_date', '')

    if away_sp is not None: return None      # away has data — no fade needed
    if home_sp is None: return None          # both missing — can't call
    if spread is None or spread > -20: return None   # not a chalky spot
    if game_dt < '2026-08-25' or game_dt > '2026-09-22': return None  # weeks 1-3 only

    return Opinion(
        signal_key='ncaaf_fbs_vs_fcs_early_season',
        signal_class='situational',
        side='HOME_RL',   # back the FBS spread
        strength=0.5,
        hit_rate=0.80,    # historical prior (per Team Ranking research)
        sample_n=200,
        tier='DISCOVERY',
        display_prose='FBS home team vs FCS opponent (unknown-data prior) — early-season chalk pattern',
    )
```

## Post-launch expansion

- Wire CFBD FCS SP+ endpoint (option #3)
- Build FCS returning production pull (analogous to FBS one)
- Add FCS-specific `ncaab_team_stats` equivalent for basketball

---

**Status**: plan documented, option #2 implementation attached (below)
as a follow-up commit.

**Owner**: pipeline (server-side; no app changes needed).

**Data sources missing**: CFBD FCS SP+ endpoint (post-launch), returning
production for non-FBS teams (post-launch).
