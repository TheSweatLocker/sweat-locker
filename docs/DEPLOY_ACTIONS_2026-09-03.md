# User Action Checklist — 2026-09-03 Launch Prep

Everything that needs to be done BY YOU (not by me) before the app goes
live. Ordered so nothing depends on a step below it.

---

## 🔴 CRITICAL — do before user-facing traffic

### 1. Deploy both edge functions

```bash
# From the repo root
supabase functions deploy claude-proxy
supabase functions deploy odds-proxy
```

Verify each shows in Supabase Dashboard → Edge Functions.

### 2. Set both API keys as Supabase secrets (server-side only)

```bash
supabase secrets set ANTHROPIC_API_KEY=sk-ant-<paste-current-key>
supabase secrets set ODDS_API_KEY=<paste-current-Odds-key>
```

Verify with `supabase secrets list` — should show both names (values
hidden). These live server-side only, NEVER in the app binary.

### 3. Apply the odds_cache migration

Open Supabase SQL Editor → paste the contents of:
```
supabase/migrations/20260904a_odds_cache.sql
```
Run it. Table `odds_cache` should appear. Migration also does
`NOTIFY pgrst 'reload schema'` so PostgREST picks it up immediately.

### 4. Smoke-test the proxies

Quickest curl test (replace `<PROJECT>` with your Supabase project ref
and `<ANON>` with the anon key):

```bash
# Claude proxy
curl -X POST https://<PROJECT>.supabase.co/functions/v1/claude-proxy \
  -H "Authorization: Bearer <ANON>" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":50,"messages":[{"role":"user","content":"say hi"}]}'
# Expect 200 with content.[0].text

# Odds proxy
curl -X POST https://<PROJECT>.supabase.co/functions/v1/odds-proxy \
  -H "Authorization: Bearer <ANON>" \
  -H "Content-Type: application/json" \
  -d '{"endpoint":"/v4/sports/baseball_mlb/odds","params":{"regions":"us","markets":"h2h","oddsFormat":"american"}}'
# Expect 200 with array of games. Headers should show x-cache: MISS first time,
# HIT on second identical call within 60s.
```

If either fails, the app will silently degrade (features that hit these
paths won't render narratives / live odds). Not a crash, but broken UX.

### 5. Apply the pending view migration (if not already done)

If you haven't applied it yet from earlier tonight:
```
supabase/migrations/20260903c_mlb_props_publishable_view_fix.sql
```
This fixed the boolean cast error on `v_mlb_props_publishable`.

### 5b. Apply the LR dissent calibration migration

```
supabase/migrations/20260904c_lr_dissent_calibration.sql
```
Creates `lr_dissent_calibration` table + `v_lr_dissent_hitrate` view.
Feeds the nightly `mlb_lr_dissent_audit.py` step already wired into
`mlb_pipeline.yml`. After a week of games you can query
`v_lr_dissent_hitrate` to see whether LR wins when it dissents vs the
ensemble. Threshold: LR ≥60% at n≥30 on mode=blocked → loosen the
consensus-dissent gate; ≤45% → gate is correctly protective.

---

## 🟡 IMPORTANT — do within 48h of shipping

### 6. Rotate the two leaked keys

The Anthropic and Odds API keys are **already embedded in every prior
build on TestFlight**. Anyone who ever downloaded a build has the raw
keys extractable from the IPA.

Rotate now = old keys become invalid = old builds start failing =
users see broken narratives/odds → forced upgrade to the new build
(which reads through the proxy, has no key in binary).

```
# Anthropic dashboard: rotate key → paste new one:
supabase secrets set ANTHROPIC_API_KEY=sk-ant-<NEW-key>

# Odds API dashboard: rotate key → paste new one:
supabase secrets set ODDS_API_KEY=<NEW-key>
```

Confirm proxies still work with the new keys (re-run curl tests from #4).

### 7. Run the NCAAF dedupe script one more time before Fri opener

```bash
cd mlb_pipeline
python _ncaaf_dedupe_resolver_2026_09_03.py --dry-run   # preview
python _ncaaf_dedupe_resolver_2026_09_03.py             # execute
```

Root cause fixed in `ncaaf_odds_pull.py`, but any dupes ingested before
the fix landed may still be around.

---

## 🟢 NICE-TO-HAVE — post-launch

### 8. Wire the Claude proxy rate limiter

`supabase/functions/claude-proxy/index.ts` has a rate-limit hook stub
at line 113. When RevenueCat user JWTs start flowing, drop in:

```typescript
const userId = /* decode auth JWT sub */;
const allowed = await checkRateLimit(userId, 'claude-proxy');
if (!allowed) return json({ error: 'Daily limit reached' }, 429);
```

Backing table:
```sql
CREATE TABLE claude_proxy_usage (
  user_id text NOT NULL,
  day date NOT NULL,
  count int NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);
```

Then flip a daily cap per RevenueCat tier (e.g. free = 20 Claude calls/day,
paid = 500). Prevents any single user from burning the budget.

### 9. Reduce `keep_alive.yml` cron cadence

Currently every 30 min = 1,440 min/month just to prevent Supabase pause.
If you're on Supabase Pro (no pause), you can drop this entirely. On
free tier, hourly (720 min) still keeps the DB warm and cuts GH Actions
minutes in half.

### 10. Sanity-check Sharp Card cache is populating each morning

After overnight cron runs, verify:
```sql
SELECT cache_key, data->>'count' AS items, generated_at
FROM jerry_cache
WHERE cache_key LIKE 'sharp_card_%'
ORDER BY fetched_at DESC LIMIT 3;
```

Should show today's date + item count. If empty or stale (>12h old),
`generate_sharp_card.py` failed in cron — check GH Actions log.

---

## Summary — what each item accomplishes

| # | Action | Fixes |
|---|---|---|
| 1 | Deploy proxies | Enables server-side API key path (app is already routed here) |
| 2 | Set secrets | Server-side keys work |
| 3 | odds_cache migration | Cache table exists → per-user cost stays flat |
| 4 | Smoke test | Catches broken deploys before users see it |
| 5 | View migration (if pending) | `v_mlb_props_publishable` works |
| 6 | Rotate keys | Kills the leaked-key abuse window |
| 7 | NCAAF dedupe | Cleans any residual dupes |
| 8 | Rate limiter | Caps any single user's Claude burn |
| 9 | keep_alive frequency | Cuts GH Actions minutes |
| 10 | Sharp Card sanity | Catches silent cron failures |

Items 1-4 are launch-blockers. If you skip them, features that hit
Claude/Odds will 500 or silently return empty because the app now
expects to talk to the proxy (not the direct APIs).

---

## What I'M doing next (no action from you needed on these)

- Track Record 20K-row scan → server aggregation (final client-side
  cost item from the audit)
- Tech-debt cleanup sweep (queued)
