# claude-proxy edge function

Server-side wrapper for every Anthropic API call the app makes. Eliminates
the `ANTHROPIC_API_KEY` from the app binary.

## Why

Pre-proxy, `ANTHROPIC_API_KEY` was hardcoded as a constant in `app/index.tsx`
and sent in `x-api-key` headers from the device. Anyone who decompiled the
IPA, ran a proxy like Charles, or even just inspected `index.bundle` could
extract the key and rack up unlimited Anthropic charges on our account. The
proxy moves the key server-side into Supabase edge function secrets, where
it never touches a client device.

## One-time setup (Saturday 5/16)

```bash
# Deploy the function
supabase functions deploy claude-proxy

# Set the server-side secret (one-time; never goes into git or the app)
supabase secrets set ANTHROPIC_API_KEY=sk-ant-...

# Verify it deployed
supabase functions list
```

Test from the CLI before wiring up the app:

```bash
curl -X POST 'https://<project-ref>.supabase.co/functions/v1/claude-proxy' \
  -H 'Authorization: Bearer <supabase-anon-key>' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Say hi in one word."}]
  }'
```

Expected: `{"content": [{"type": "text", "text": "Hi"}], "usage": {...}, ...}`

## App-side migration (Saturday 5/16)

Add a single helper near the top of `app/index.tsx`:

```typescript
// Wraps every Anthropic call through the server-side proxy. Same request
// shape as Anthropic's /v1/messages API — drop-in replacement for the
// existing direct fetch() calls. The ANTHROPIC_API_KEY constant can be
// deleted from app/index.tsx entirely after this is wired in.
const callClaudeProxy = async (body: {
  model?: string;
  max_tokens: number;
  messages: Array<{role: string; content: string}>;
  system?: string;
  tools?: any[];
  temperature?: number;
}) => {
  const { data, error } = await supabase.functions.invoke('claude-proxy', {
    body,
  });
  if (error) throw error;
  return data;  // shape matches Anthropic's response — content[].text etc.
};
```

Then find/replace each direct call:

```typescript
// BEFORE:
const response = await fetch('https://api.anthropic.com/v1/messages', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'x-api-key': ANTHROPIC_API_KEY,
    'anthropic-version': '2023-06-01',
    'anthropic-dangerous-direct-browser-access': 'true'
  },
  body: JSON.stringify({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 1000,
    messages: [{role: 'user', content: prompt}]
  })
});
const data = await response.json();

// AFTER:
const data = await callClaudeProxy({
  model: 'claude-haiku-4-5-20251001',
  max_tokens: 1000,
  messages: [{role: 'user', content: prompt}]
});
```

Touchpoints to migrate (current `app/index.tsx`):

| Function | Approx line | Notes |
|---|---|---|
| `fetchParlayAnalysis` | ~4013 | Uses `tools: [web_search_...]` — pass-through works |
| `fetchPickRecap` | ~4078 | Simple |
| `fetchGameNarrative` (fallback path) | ~5645 | Cache path already skips Claude — only fallback hits proxy |
| Best-prop blurb | ~6119 | Simple |
| Daily-chat / supabase-cached chat | ~4524 | Simple |

After all touchpoints are swapped, delete the `ANTHROPIC_API_KEY` constant
from the top of `app/index.tsx`. Confirm with `grep`:

```bash
grep -n "ANTHROPIC_API_KEY\|api.anthropic.com" app/index.tsx
```

Expected: zero hits.

## What's enforced server-side

- **Model allowlist** — only Haiku 4.5 (no expensive Opus / Sonnet abuse)
- **`max_tokens` cap of 4000** — prevents runaway 100K-token responses
- **Prompt size cap of 60K chars** — prevents context-stuffing
- **45s timeout** — Anthropic + web_search can be slow; longer = hang
- **Status code pass-through** — app sees real 429 / 529 and handles them

## Not in v1 (post-launch additions)

- **Per-user rate limiting.** Hook is left in the function (see `Rate-limit
  hook` comment). Wire it up once RevenueCat is integrated and user IDs
  flow in via the auth JWT. Suggested limits: 20 parlay analyses/day, 10
  chats/day per free-trial user.
- **Audit log table.** Capture (user_id, endpoint, tokens_in, tokens_out,
  cost_estimate) per call once the rate limiter is in.
- **Request signing / nonce.** Prevents replay attacks. Low priority for
  the launch surface — supabase anon key auth is sufficient initially.
