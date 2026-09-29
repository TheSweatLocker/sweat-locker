---
name: project-ui-toggle-infrastructure-908
description: "🚨 9/8 architectural gap: too many UI decisions hardcoded client-side → each fix costs a full binary submission cycle. v1.0.1 must ship a server-controlled render manifest before we accumulate more."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T14:18:27.225Z
---

**User observation · 9/8 late morning:** "Dont like the fact that we already have a hardcode issue when we're waiting on app submission thoughts?"

Team Matchup vs Team Stats redundancy on NFL Game Detail is hardcoded client-side ([app/components/GameDetailV2.tsx](app/components/GameDetailV2.tsx) lines 2762 + 3436). Any fix requires a v1.0.1 submission. And this is just the FIRST example noticed post-submit — the app has hundreds of similar hardcoded UI decisions.

## The gap

Right now, every section, chip label, tab name, tier threshold display, and help copy is hardcoded in React Native. Any change to any of them requires:
1. Code edit
2. EAS build (~30 min)
3. TestFlight processing (~15 min)
4. Sanity check on real device
5. App Store submit → Apple review (~24-48 hrs, sometimes rejection cycle)
6. Manual release

**Total cost per UI decision: 2-4 days.** For the lifetime of the app, this compounds badly.

## The solution — server-controlled render manifest

For layout decisions (NOT pick logic — that's already server-side), the client should read a config table on boot and render accordingly.

**Schema (draft):**
```sql
create table config_ui_sections (
  sport text not null,          -- e.g. 'NFL', 'MLB', or 'ALL'
  surface text not null,        -- e.g. 'game_detail', 'games_tab', 'home'
  section_key text not null,    -- 'team_matchup', 'team_stats', 'money_flow', etc.
  enabled boolean default true,
  order_idx int default 100,
  label_override text,          -- optional display label override
  hint_override text,           -- optional hint override
  primary key (sport, surface, section_key)
);
```

**Client change (per component):**
```tsx
{isSectionEnabled('nfl', 'game_detail', 'team_matchup') && (
  <Section title={sectionLabel(...) || 'Team Matchup'} ...>...</Section>
)}
```

**Result:** removing Team Matchup for NFL = one SQL edit:
```sql
update config_ui_sections
set enabled = false
where sport = 'NFL' and surface = 'game_detail' and section_key = 'team_matchup';
```

Zero rebuild. Live in the app within seconds (client refetches config on foreground).

## Scope for v1.0.1 build

**In-scope (~4 hours):**
- Migration + seed defaults for every current section
- `isSectionEnabled(sport, surface, section_key)` helper hooked to a client-cached config fetch
- Wrap every `<Section>` in GameDetailV2 + index.tsx behind the check
- Cache invalidation (5-min TTL or Supabase realtime subscription)
- Default: every section enabled (parity with current app)

**Out of scope for v1.0.1 (later):**
- Config-driven ORDERING (harder — requires refactor of hardcoded section order in render loop)
- Config-driven LABELS beyond simple string overrides
- Admin UI for editing the config (SQL is fine for v1.0.1)

## What we get after v1.0.1 lands

Any of these become SQL edits, no rebuild:
- Remove Team Matchup (redundant with Team Stats)
- Rename "The Sharp" to something else if user calls it out
- Hide the Ladder for users in restricted regions
- Reorder sections per sport (e.g. put MoneyFlow above Team Stats for NFL, below for MLB)
- A/B test section labels
- Kill any section that isn't performing without eating a review cycle

## The bigger design principle going forward

**Before hardcoding any new UI section, ask: is this PERMANENT?** If there's any chance we'll rename/hide/reorder it, wrap it in the toggle from the start. Cost is ~1 extra line of code per section; savings are enormous.

Applies to:
- Section renders (`<Section>`)
- Chip labels (`{callVerdictColor.label}`)
- Tab names (`{id:'sharp',label:'🎯 The Sharp'}`)
- Empty-state copy
- Tier threshold displays
- Any string that could plausibly change

## Priority sequencing (fold into launch plan)

**v1.0 (already submitted):** ship as-is with the redundancy. Not a launch blocker.

**v1.0.1 first sprint (post-approval, target 9/15-9/20):**
1. Ship this toggle infrastructure ← foundational
2. Configure via SQL: hide NFL Team Matchup (the redundancy user noticed)
3. Any other UI-only fixes from user feedback in the first days post-launch
4. Batch all UI fixes into this ONE binary submission

**Every subsequent UI decision:** SQL edit, no rebuild.

## Related
- [[project_data_infrastructure_priorities_908]] — data foundation priorities (LR + grading + morning brief)
- [[project_website_update_queue_908]] — website content parity work (parallel post-launch)
- [[feedback_backside_dictates_app_renders]] — same principle: backend controls what app renders
