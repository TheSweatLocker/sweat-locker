---
name: jerry-read-opp-lineup-attribution-fix-5-16
description: "Jerry MLB game reads were mixing up which team's wRC+ a pitcher faces (Tolle/Boston/Atlanta case). Fixed 2026-05-16 by pre-computing pitcher.opp_lineup_wrc in struct."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## The bug

Jerry's BOS/ATL game read on 2026-05-16 contained this contradiction:

> *"Tolle... holds a +7.8 point K-rate advantage against this lineup"* ✓ (correct, Tolle vs ATL)
> *"Atlanta's 118 wRC+ represents genuine offensive muscle"* ✓
> 🚨 *"Boston counters at 90 wRC+, a soft lineup that Tolle's strikeout profile will punish"*

That third sentence is wrong — Tolle pitches FOR Boston. The 90 wRC+ is his own team's offense, not the lineup he faces. The actual opp lineup wRC+ is Atlanta's 118.

Same family of attribution bug as the Suarez/Luzardo 5/14 mixup (see [[user_2026_roster_corrections.md]] and [[feedback_verify_pitcher_attribution.md]]). The data was correct in the JSON — `home_wrc_plus: 118`, `away_wrc_plus: 90` — but Claude had to mentally map "away pitcher → faces home lineup → use home_wrc_plus" and the mapping was brittle. Got the K-gap sentence right (because that field had built-in side encoding), then drifted on subsequent sentences.

## The fix

**`mlb_pipeline/generate_mlb_game_reads.py:_pitcher_block`** now pre-computes per-pitcher opp fields so Claude can't lose the mapping:

```json
"pitchers": {
  "away": {
    "name": "Payton Tolle",
    "own_team": "Boston Red Sox",
    "opp_team": "Atlanta Braves",
    "opp_lineup_wrc": 118.0,
    "opp_lineup_k_pct": 23.2,
    "k_gap_vs_opp": 7.8,
    ...
  }
}
```

No more "figure out which wRC+ goes with which pitcher" — it's stamped on the pitcher block.

**`prompt_templates.game_read_universal`** now has an explicit `ATTRIBUTION SAFETY` block instructing Claude to:
- Always use `pitchers.[side].opp_lineup_wrc` when describing what a pitcher faces
- Never write sentences like "[own-team]'s wRC+ that [own-team's-pitcher] will punish"
- If unsure, check `pitchers.[side].opp_team` — it's canonical

## Verification

After fix, the new struct for BOS/ATL has:
- `pitchers.away.opp_team = "Atlanta Braves"` (Tolle faces ATL ✓)
- `pitchers.away.opp_lineup_wrc = 118.0` (the right number ✓)
- `pitchers.home.opp_team = "Boston Red Sox"` (Elder faces BOS ✓)
- `pitchers.home.opp_lineup_wrc = 90.0` (the right number ✓)

Local regen couldn't call Claude (no ANTHROPIC_API_KEY in local .env), but the data struct is correct. Next cron run regenerates with the new template rule + new struct.

## Audit items for 5/17

1. **Sweep other recent Jerry reads** for the same bug — look for sentences mentioning "[Team A]'s wRC+ [Team A's pitcher] will punish/dominate" patterns. If found, validate that those reads also need regen with the new attribution.

2. **Check NBA / UFC / NFL templates** for analogous attribution issues. NBA has home/away offense too — same potential for "Pacers' rating that Pacers' star will exploit" bugs. NFL even more so.

3. **Add a unit-test-style check** in generate_mlb_game_reads.py: after building struct, assert that `pitchers.home.opp_lineup_wrc == situational.away_wrc_plus` and vice versa. Catches future regressions immediately.

## Related
- [[feedback_verify_pitcher_attribution]] — the 5/14 Suarez/Luzardo lesson that this same family
- [[user_2026_roster_corrections]] — 2026 roster moves (Suarez/Alonso) compound the risk
- [[project_dod_confluence_bug]] — same-day fix; both were "scorer trusted raw fields without canonical attribution"
