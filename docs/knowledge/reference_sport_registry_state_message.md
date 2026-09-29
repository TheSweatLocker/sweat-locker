---
name: reference-sport-registry-state-message
description: sport_registry.state_message is the sport-tab warning/status banner. Live-editable via SQL — no app rebuild for tone/copy changes.
metadata: 
  node_type: memory
  type: reference
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-17T20:44:15.735Z
---

**Table:** `public.sport_registry`

**Field:** `state_message` — the sport-scoped banner/status message rendered on the corresponding sport tab. Rendered live, polled every 5 min by the client.

**How to change any sport's tab message:**

```sql
UPDATE sport_registry
   SET state_message = '<your new message>',
       updated_at = NOW()
 WHERE sport = '<SPORT>';   -- MLB / NFL / NCAAF / NBA / NHL / NCAAB / UFC
```

**Also on sport_registry (all live-editable):**
- `state` — 'in_season' / 'offseason' / 'preseason' / 'testing'
- `today_note` — hint text under "today's slate" heading
- `tomorrow_note` — hint text on the "next week / tomorrow" tab
- `tab_scope` — 'daily' / 'weekly' / 'weekend' render pattern

**Example (Andy 9/17):** UFC tab was showing "Calibrator v1 (n=53) LIVE 2026-08-21 — probs damped to observed rates" — internal engineering language. One UPDATE replaced with user-facing "UFC modeling in early calibration phase — sample size below confidence threshold. Play smaller units on UFC picks until sample matures."

**When to use vs admin_notice vs home_banners:**
- `sport_registry.state_message` — persistent status/tone for the SPORT TAB itself (calibration state, offseason messaging). Sport-scoped.
- `admin_notice` — top-of-app banner for OPERATIONAL messages (info / warning / critical). Cross-screen.
- `home_banners` (2026-09-17d) — hot-streak / product marketing on Home tab. Rotating, priority-sorted.

Three surfaces, three purposes. Don't cross the streams.
