// supabase/functions/claude-proxy/index.ts
//
// Server-side wrapper for Anthropic API calls. The app POSTs here instead of
// hitting api.anthropic.com directly — eliminates the ANTHROPIC_API_KEY from
// the app binary entirely (it lives only in edge function secrets, server-
// side, not extractable by decompiling the IPA).
//
// Deploy:
//   supabase functions deploy claude-proxy
//
// Set secrets (one-time, server-side only):
//   supabase secrets set ANTHROPIC_API_KEY=sk-ant-...
//
// Request body (matches Anthropic /v1/messages shape so app code stays close
// to the existing pattern):
//   {
//     "model": "claude-haiku-4-5-20251001",
//     "max_tokens": 1000,
//     "messages": [{"role": "user", "content": "..."}],
//     "system": "optional system prompt",
//     "tools": [{"type": "web_search_20250305", "name": "web_search"}],
//     "temperature": 0.7
//   }
//
// Response: Anthropic's response body, passed through transparently. The app's
// existing parsing logic for `data.content[].text` continues to work unchanged.
//
// Defense layers built in:
//   1. Model allowlist — prevents abuse via expensive models (no Opus, no Sonnet)
//   2. max_tokens hard cap — prevents runaway 100K-token responses
//   3. Prompt size limit — prevents context-stuffing attacks
//   4. 45s timeout — Anthropic can be slow with web_search tools; longer than
//      that is almost certainly a hang
//   5. Pass-through error codes — app gets the real upstream status so it can
//      handle 429 (rate limit) / 529 (overload) appropriately
//
// NOT included in this draft (queued for post-launch):
//   - Per-user rate limiting (needs RevenueCat user IDs first)
//   - Request signing / nonce (anti-replay)
//   - Audit logging table
// See "Rate-limit hook" comment below for where to wire those in.

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";

// Allowed models — keep this list tight. The app uses Haiku 4.5 everywhere
// currently. Adding Sonnet/Opus access here without a paywall = expensive
// abuse vector.
const ALLOWED_MODELS = new Set<string>([
  "claude-haiku-4-5-20251001",
  "claude-haiku-4-5",
]);

const MAX_TOKENS_CAP = 4_000;          // hard cap regardless of request
const MAX_PROMPT_CHARS = 60_000;       // ~15K tokens — generous but bounded
const ANTHROPIC_TIMEOUT_MS = 45_000;   // Claude can be slow with web_search

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

serve(async (req: Request) => {
  // CORS preflight
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS_HEADERS });
  }
  if (req.method !== "POST") {
    return json({ error: "POST only" }, 405);
  }

  // Server-side key — never in the client binary
  const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
  if (!apiKey) {
    console.error("[claude-proxy] ANTHROPIC_API_KEY missing from secrets");
    return json({ error: "Server not configured" }, 500);
  }

  // Parse body
  let body: any;
  try {
    body = await req.json();
  } catch {
    return json({ error: "Invalid JSON" }, 400);
  }

  // Validate model
  const model = String(body.model || "claude-haiku-4-5-20251001");
  if (!ALLOWED_MODELS.has(model)) {
    return json({ error: `Model ${model} not allowed` }, 400);
  }

  // Validate max_tokens
  const maxTokens = Math.min(
    Math.max(Number(body.max_tokens) || 1000, 1),
    MAX_TOKENS_CAP,
  );

  // Validate messages
  const messages = Array.isArray(body.messages) ? body.messages : [];
  if (messages.length === 0) {
    return json({ error: "messages required" }, 400);
  }

  // Size check — protects against context-stuffing
  const totalChars =
    JSON.stringify(messages).length + (body.system ? String(body.system).length : 0);
  if (totalChars > MAX_PROMPT_CHARS) {
    return json({ error: "Prompt too large" }, 413);
  }

  // ── Rate-limit hook (left as a no-op in v1) ──
  // When RevenueCat user IDs flow in via the auth JWT, gate here:
  //   const userId = req.headers.get('authorization')?.split(' ')[1]; // decode JWT, get sub
  //   const allowed = await checkRateLimit(userId, 'claude-proxy');
  //   if (!allowed) return json({ error: 'Daily limit reached' }, 429);
  // Track via a small `claude_proxy_usage` table: (user_id, day, count).

  // Build Anthropic request — pass through optional fields
  const anthropicReq: Record<string, unknown> = {
    model,
    max_tokens: maxTokens,
    messages,
  };
  if (body.system) anthropicReq.system = body.system;
  if (body.tools) anthropicReq.tools = body.tools;
  if (body.temperature !== undefined) anthropicReq.temperature = body.temperature;
  if (body.thinking) anthropicReq.thinking = body.thinking;
  if (body.tool_choice) anthropicReq.tool_choice = body.tool_choice;

  // Forward to Anthropic with timeout
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), ANTHROPIC_TIMEOUT_MS);

  try {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(anthropicReq),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    // Pass Anthropic's response transparently — app's existing parser handles
    // the {content: [{type:'text', text:'...'}]} shape unchanged.
    const responseText = await r.text();
    return new Response(responseText, {
      status: r.status,
      headers: {
        ...CORS_HEADERS,
        "Content-Type": "application/json",
      },
    });
  } catch (e) {
    clearTimeout(timeoutId);
    if ((e as Error).name === "AbortError") {
      return json({ error: "Anthropic API timeout" }, 504);
    }
    console.error("[claude-proxy] upstream error:", (e as Error).message);
    return json({ error: "Upstream error" }, 502);
  }
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
