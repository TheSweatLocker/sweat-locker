# Brewers Deep-Dive + Slate Signal Audit — 2026-08-23

**Game:** Atlanta Braves @ Milwaukee Brewers · American Family Field (dome, park factor 97)
**game_id:** `bda345f597a8317d5812e066bfdd364a`
**Lines:** MIL ML -113 (open -130), Spread -1.5, Total 7.5
**Primary play:** Milwaukee Brewers ML — **PRIME conv 88** (score 1.33, margin +1.11; user quoted 84, live=88 as of 18:59 UTC).

---

## 1. Brewers Deep Dive

### 1a. Ensemble sources driving MIL ML (13 considered, 8 fired)

| signal_key | class | n | wt | contrib |
|---|---|---:|---:|---:|
| `confluence_home_lean` | cohort | 91 | 0.83 | **+0.42** |
| `home_ml_hot_at_home` | team_form | 16 | 0.50 | +0.28 |
| `home_offense_hot_l7` | offense | 24 | 0.50 | +0.15 |
| `sharp_scenario_match:rlm_sharp_away` | scenario | 31 | 0.56 | +0.14 |
| `confluence_home_lean_as_coin` | cohort | 40 | 0.17 | +0.09 |
| `external:pickdawgz` | external | 127 | 0.16 | +0.08 |
| `sharp_scenario_match:money_55-64_home` | scenario | 23 | 0.50 | +0.07 |
| `jerry_pred_spread` | model | 131 | 0.08 | +0.04 |

Cohort trio = 33%+ of stack. Signal #4 explicitly fades RLM sharp side (61.3% n=31, see 1f).

### 1b. Pitcher matchup

| | Drohan (LHP, HOME) | Mahle (RHP, AWAY) |
|---|---|---|
| xERA / SIERA | 3.62 / 2.88 | 4.17 / 3.02 |
| L3 ERA / K% | **6.06** / 22.9% | **1.45** / 27.5% |
| Last outing | 2.0 IP · 39 P (short leash) | 6.7 IP · 76 P |
| Home / Road ERA | 4.21 / 3.37 | 2.53 / **6.55** here |
| Projected | 4.4 K · 4.1 H · 2.1 ER · 13.3 outs | 4.9 K · 4.7 H · 2.0 ER · 16.7 outs |
| vs opp career | none | 3.86 ERA, recent 6 IP / 0 ER (n=1) |

Mahle better rates + hot L3, road-ERA trap. Both project short (~4.4/~5.6 IP) → 8-10 relief IP, feeding cohort UNDER.

### 1c. Bullpen

MIL: availability **20%** (thin, 5 P IL: Uribe, Woodruff, Romero…) · BP ERA 3.42 / late 3.20 · used 8 in L3d.
ATL: availability 30% (5 P IL: Suarez, Jiménez, López…) · BP ERA 3.61 / late 3.55 · used 7 in L3d.
Both taxed → cohort tags "bullpen_taxed_either" → 81% UNDER n=21 on 30d.

### 1d. Offense heat

| | MIL | ATL |
|---|---|---|
| OPS L7 / L14 / season | **.851** / .735 / .733 | **.573** / .588 / .721 |
| wRC+ season / vs hand | 109 / 106 vs RHP | 105 / 101 vs LHP |
| L10 rec · L5 RPG | 7-3 · **8.0** | 3-7 · **1.8** |
| Offense drift L7 · streak | **+0.92** · W3 | **-2.19** · L2 |

**3.11-run hot/cold gap** — largest split in the game. ATL is dead cold.

### 1e. Weather / park

Dome. 72°F, wind 0, park factor 97. No weather angle — most wind/temp VALIDATED signals unfireable.

### 1f. Splits (OC / FR / CZ / SO — 4/4 sources, no dissent)

| Mkt · Side | Bets% | Money% | Handle% | Div |
|---|---:|---:|---:|---:|
| ML · HOME | 66.8 | 51.7 | 57.0 | 3 |
| ML · AWAY | 33.2 | 48.3 | 47.5 | **17 RLM** |
| Total · OVER | 74.3 | 60.0 | 88.0 | -1.5 |

ML line moved -130→-113 (toward ATL) with bets 66.8% HOME → RLM. `sharp_side=A`, `money_side=H`, verdict `no_data`. Yellow flag against MIL — pipeline knowingly fades (signal #4).

### 1g. Monte Carlo

| Metric | Value | Edge vs implied |
|---|---:|---|
| mc_p_home_win | **0.569** | +3.9pp vs 53.1% at -113 |
| mc_p_away_win | 0.287 | ATL priced short |
| mc_expected_margin | +0.74 HOME | Jerry 1.52 · v3 1.59 |
| **mc_p_under** | **0.632** | **+10.8pp** vs 52.4% at -110 |
| mc_mean_total | 6.79 vs line 7.5 | |

### 1h. Props (12 published — ALL tier=LEAN, none reached STRONG/PRIME)

| Player | Prop | Line | Dir | Conv | **Refit** |
|---|---|---:|---|---:|---:|
| Tyler Mahle | Ks | 5.5 | UNDER | 55 | **81** (proj 4.9 K, capped) |
| Shane Drohan | Ks | 4.5 | UNDER | 55 | **81** (proj 4.4 K, capped) |
| Andrew Vaughn | hits | 0.5 | OVER | 61-64 | 59.7 (6/7 L7, .350 BA, bats 5) |
| Drohan / Mahle | ER, walks, outs, HA | mixed | | 55 | 0-43 |

**Two refit=81 K UNDER props sit at LEAN** — juice-trap / gate discipline. Best individual prop signals in the game, but they never reach the card.

---

## 2. Slate Signal Audit (15 games)

### 2a. Top fired signal keys (primary picks)

| signal_key | fires | avg contrib |
|---|---:|---:|
| `confluence_home_lean` | 8 | **+0.505** |
| `sharp_confluence_alignment` | 3 | **+0.590** (top avg) |
| `home_offense_hot_l7` | 5 | +0.150 |
| `external:sbr` | 3 | +0.390 |
| `home_sp_owned_by_away_total` | 2 | +0.420 |
| `home_ml_hot_at_home` | 3 | +0.280 |
| `sharp_scenario_match:rlm_sharp_away` | 4 | +0.140 |
| `v4_model_spread__fade` | 5 | +0.040 |

Cohort trio dominates as designed. **v4 fires as a FADE 5× — sweat_breakdown carries a live warning that v4 called OVER on >75% of 14d games.**

### 2b. Top 5 dead signals (enabled MLB `signal_sources` never fired tonight)

164 of 203 enabled MLB signal_sources (81%) didn't fire.

| signal_key | scope | probable cause |
|---|---|---|
| `home_pitcher_ice_cold` | multi | threshold too strict; Drohan L3 6.06 missed cut |
| `mc_high_confidence` | ml | `mc_high_conf_flag=True` set on 0 games |
| `home_pitcher_on_heater` / `away_pitcher_on_heater` | multi | Mahle L3 1.45 should qualify — check condition_expr |
| `home_ace_by_xera` / `away_ace_by_xera` | total | xERA cutoff not met; Drohan 3.62 close |
| `h2h_under_after_overs` | total | narrow pattern; no matches |

### 2c. Top 5 missed-edge (VALIDATED registry, edge_pp ≥ 5, not fired)

| signal_name | scope | edge_pp | n | why not firing tonight |
|---|---|---:|---:|---|
| `slow_start` | outs_under | **+32.0** | 64 | prop pipeline caps all outs_under to LEAN |
| `book_recalibration` | outs_under | **+30.0** | 68 | same gate |
| `l5_confirm` | outs_under | **+29.9** | 62 | same gate |
| `nrfi_temp_le_45` | total | **+26.8** | 60 | temp≤45°F — no cold game (dome for MIL) |
| `prop:wind` | prop | **+24.4** | 95 | dome + calm slate; unfireable |

**Pattern:** most missed-edge VALIDATED signals live in the prop pipeline. MIL/ATL props with refit=81 capped to LEAN is the same suppression — a VALIDATED +30pp signal that can't express is a signal bug (project_signal_framework_821).

### 2d. Contested-totals watch (slate-wide)

5/15 games have ensemble total STRONG/PRIME with resolver = SKIP:

| game | ensemble total | dissent |
|---|---|---|
| **ATL @ MIL** | Under 7.5 STRONG (75) | models OVER vs cohort -19.5 UNDER + props OVER |
| LAA @ TEX | Under 8.0 PRIME (87) | signals don't agree |
| CLE @ COL | Over 11.0 STRONG (82) | signals don't agree |
| ATH @ HOU | Over 9.0 STRONG (79) | cohort -25.5 UNDER |
| PIT @ LAD | Over 8.5 PRIME (93) | cohort -15.5 UNDER |

**Systemic:** cohort anti-correlates with v3/v4 on 4/5 total contests → v4 OVER-bias polluting resolver, or cohort over-weighting bullpen-taxed in late-August.

---

## 3. Best-Bet Recommendation

### Candidates

| Play | MC edge | Pipeline verdict |
|---|---:|---|
| **MIL ML -113** | +3.9pp | PRIME 88 |
| Under 7.5 | **+10.8pp** | Ensemble STRONG 75 · **Resolver SKIP** |
| Mahle K U 5.5 / Drohan K U 4.5 | strong | LEAN (refit 81, gate-capped) |

### Call: **Milwaukee Brewers ML -113 · PRIME · 1u standard**

1. **Discipline over raw EV.** Under 7.5 has ~3× the MC edge but resolver flagged SKIP on model dissent — overriding on cohort+MC alone ignores the same discipline that saved cards from v4 OVER-traps in the 8/14 variance lesson.
2. **Signal stack is real.** 8/13 sources aligned; top signal n=91, wt 0.83; cohort STRONG_EDGE 68.1% historical (56-22, n=78).
3. **Spot fits calibration.** ATL vs LHP wRC+ 101, L7 OPS .573, L2 streak — cold offense against Drohan's whiff/GB profile in a pitcher park is the calibration slice.
4. **RLM warning priced in.** `sharp_scenario_match:rlm_sharp_away` (61.3% n=31) knowingly fades sharp side on prior pattern.

### Shadow-track (do not publish)

- **UNDER 7.5** — track to strengthen resolver-dissent audit. Cohort UNDER beating models on another taxed-bullpen game is evidence to demote v4 in resolver for late-August fatigue.
- **Mahle K U 5.5** — refit=81 on a VALIDATED prop pattern capped to LEAN is the exact suppression flagged in 2c.

### Avoid

- **Under 7.5 public** — resolver dissent + 3 signal directions = "let the engine speak" spot.
- **ATL side/RL** — no data supports; road-cold + Mahle road-ERA 6.55 trap.
