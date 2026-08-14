# Sweat Locker Rollback Playbook

**Purpose**: when a rule/model/component starts causing problems in production,
this document tells you exactly what to disable, how, and how to verify the
rollback took effect. Living document — update every time a new
rollback-eligible component ships.

**Golden rule**: fastest safe rollback beats prettiest fix. Ship the disable
first, investigate root cause second, ship the proper fix third.

---

## Bug disclosure decision framework

Before every incident response, run this test:

> **"Would a user reviewing their own notes/screenshots see something
> inconsistent with what our current pipeline says?"**

| Scenario | Action |
|----------|--------|
| Bug caught via monitoring, user-visible output unaffected | Quiet fix. Log to `dashboard_alerts` with `severity=info`. |
| Bug affected analysis internally but wasn't published to users | Quiet fix. Log to `dashboard_alerts`. Post-mortem in this doc. |
| Bug caused users to see analysis different than what current pipeline generates | In-app note (see `user_notes` table in Session D). |
| Bug caused significantly-different published picks over multiple days | In-app note + track-record disclosure. Optionally re-render historical track record. |
| Bug affected paid-subscriber-only surface | Same as above, plus include subscribers in the audience filter for the in-app note. |

**Default is quiet fix.** Session A/B/C monitoring should catch most bugs fast
enough that they never reached users in the first place. That's the design.

---

## Rollback catalog

### Category · Refit override rules

**Component**: `apply_refit_verdict_override.py` rules
**Blast radius**: `prop_jerry_reads.call_verdict` mutations → downstream sweat
card composition, top_props curation, Jerry-anchored Daily Degen
**Rollback method**:
```
python promote_rule.py set <RULE_NAME> off --reason "<one-line why>"
```
Rules currently in registry (as of 2026-08-14):
- `FORCE_FADE_TRAP` — DISABLED (12% hit rate, historical audit)
- `FORCE_PASS_CONFLICT` — DISABLED (killed 63% winners)
- `FORCE_PASS_JERRY_HALLUCINATION` — ACTIVE
- `FORCE_BACK_BOOST` — ACTIVE
- `FORCE_BACK_REFIT_OVERRIDE` — ACTIVE
- `FORCE_BACK_FLIP_LEAN_CAP` — ACTIVE
- `REFIT_BAND_UNPROVEN` — ACTIVE
- `NO_REFIT_CAP` — ACTIVE

**Effect delay**: 5 min (rule mode cache TTL in `rule_registry.py`)
**Verification**:
```sql
SELECT rule_name, mode, updated_at
FROM rule_registry
WHERE rule_name = '<RULE_NAME>';
```
Confirm `mode='off'` + `updated_at` matches your action.
```sql
SELECT COUNT(*) FROM rule_shadow_log
WHERE rule_name='<RULE_NAME>' AND applied=true
  AND event_ts > NOW() - INTERVAL '15 minutes';
```
Should be zero within 15 min of toggle.

**Recovery / re-grading**: refit-driven verdict changes are logged with
audit_notes. If you need to reverse the effect on already-published picks,
run (future work):
```
python regrade_verdicts.py --rule <RULE_NAME> --since <YYYY-MM-DD>
```
Not yet built — flag as follow-up when first needed.

---

### Category · Pipeline repair rules

**Component**: `jerry_pre_publish_audit.py --repair` classes
**Blast radius**: same as refit — modifies `prop_jerry_reads` / `jerry_reads`
before publish
**Rollback method**: same `promote_rule.py set X off` pattern for rules in registry.
For built-in classes not yet migrated to registry, temporarily edit
`jerry_pre_publish_audit.py` and comment out the class call, then push.
**Effect delay**: next pipeline cycle (up to 30 min).
**Verification**: check `rule_fire_stats` next day — rule should have zero
fires in the 1d window.

---

### Category · Prop tier calibration

**Component**: `prop_tier_calibration.py` FADE_COMBOS + GOLDMINE_SKIP_COMBOS
**Blast radius**: `mlb_pipeline_props.tier` assignments → sweat card
composition, prop_jerry synthesis
**Rollback method**: edit the FADE_COMBOS dict, remove offending entry,
push. Auto-refresh from `prop_bucket_roi` on next import can also modify —
if the auto-refresh is producing bad entries, temporarily disable
`_refresh_from_live_data()` at line ~176.
**Effect delay**: next cron cycle (30-60 min).
**Verification**: query `mlb_pipeline_props` for the affected prop_type +
tier combo — should not have the FADE-flipped direction after next run.

---

### Category · NFL 5-model lenses

**Component**: `nfl_mc_simulator.py`, `nfl_v3_regression.py`,
`nfl_v4_inference.py`
**Blast radius**: `nfl_game_context` mc_probabilities / v3_spread / v4_spread
columns → downstream compute_primary_play lens count
**Rollback method**: for MC/V3 (rule-based): comment out the corresponding
step in `.github/workflows/nfl_pipeline.yml`. For V4 (model-based): delete
or move `mlb_pipeline/models/nfl_v4_spread.pkl` and `nfl_v4_total.pkl` —
inference script no-ops gracefully when models missing.
**Effect delay**: next NFL cron cycle (Tue/Wed/Thu/Sat/Sun/Mon).
**Verification**: `nfl_game_context.mc_probabilities` (or v3/v4) should be
NULL on new rows. compute_primary_play automatically drops that lens from
its agreement count.
**Recovery**: re-enable workflow step. Next cycle repopulates.

---

### Category · Steam Room detector

**Component**: `detect_line_movement.py`
**Blast radius**: `line_movement_flags` populated → Steam Room UI cards
**Rollback method**: comment out the detector step in `mlb_pipeline.yml`.
Existing flags stay; no new ones written.
**Effect delay**: next cron cycle.
**Verification**: `line_movement_flags` last_seen_at should stop advancing.

---

### Category · Sport-registry state (per-sport UX flip)

**Component**: `sport_registry` table
**Blast radius**: per-sport UX in app — tab labels, empty-state notes, ladder eligibility
**Rollback method**:
```sql
UPDATE sport_registry
SET state='in_season', state_message=NULL, tab_scope='daily'
WHERE sport='<SPORT>';
```
**Effect delay**: next app reload (~cached hourly in app).
**Verification**: `SELECT * FROM sport_registry WHERE sport='<SPORT>'`.

---

### Category · Ladder engine

**Component**: `steam_room_ladder.py`
**Blast radius**: `ladder_state` + `ladder_rung` writes → Steam Room ladder UI
**Rollback method**: manually reset ladder_state to waiting:
```sql
UPDATE ladder_state SET status='waiting', active_rung_id=NULL, note='Manual reset — <reason>' WHERE id=1;
```
For a stronger disable, comment out the step in `mlb_pipeline.yml`.
**Effect delay**: immediate (state), next app fetch (UI).

---

## Emergency contact / decision tree

**During an incident**:

1. **Assess blast radius** — which surface is affected? (Games tab, sweat card, prop cards, Steam Room)
2. **Apply rollback** using the appropriate section above
3. **Verify** with the SQL/queries listed
4. **Log the incident** to `dashboard_alerts` with severity + category + message
5. **Decide on user comms** via the disclosure framework above
6. **Post-mortem** in this doc (add a new section under "Incident log" below)
7. **Root-cause fix** — ships via normal PR process, but AFTER the disable ships

**Don't**:
- Push a "fix" without first disabling the broken path
- Skip verification just because rollback "looked like it worked"
- Send user comms without checking the disclosure framework
- Auto-re-enable something you disabled without documenting why it's safe now

---

## Incident log

Format: `YYYY-MM-DD · <one-line title> · commit-sha`

- `2026-08-14 · FORCE_FADE_TRAP disabled — 12% hit rate over 30d · 70b3c793`
- `2026-08-14 · FORCE_PASS_CONFLICT disabled — killed 63% winners · 9d823215`
- `2026-08-14 · FADE_TYPE_BOMB list shipped — outs_over/er_under/bb/ks types converted to PASS · 356d09c0`
- `2026-08-13 · last_outing splits[0] bug — pitcher signals used debut game all season · a48cbea5`
- `2026-08-13 · Anti-consensus label rewrite — clarity fix · 6c33b11d`

(Grow this list every incident. Include the commit SHA of the disable/fix.)
