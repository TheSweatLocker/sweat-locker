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

  // ══════════════════════════════════════════════════════════════════════
  // PROVIDER SEAM — 2026-09-29
  //
  // Andy, after the key-abuse incident: "How fast could we get another provider
  // wired up and would it need a client hardcode change or is it all back end?
  // I want to wait so i avoid having to pay another provider."
  //
  // Answer, implemented here: ALL BACKEND. No client change, no App Store
  // build, no review. The app talks only to this function, sends an
  // Anthropic-shaped body and parses an Anthropic-shaped response — so the
  // translation happens on both sides of this fetch and the client never knows.
  //
  // Default is anthropic, so nothing changes and no second account is needed
  // until the day it is flipped. To flip:
  //   supabase secrets set LLM_PROVIDER=openai_compatible
  //   supabase secrets set LLM_API_KEY=...
  //   supabase secrets set LLM_MODEL=<that provider's model id>
  //   supabase secrets set LLM_BASE_URL=...   (optional, defaults to OpenAI)
  //   supabase functions deploy claude-proxy
  //
  // 'openai_compatible' rather than a named vendor because OpenAI, Groq,
  // Together, DeepSeek, Mistral and OpenRouter all speak /chat/completions with
  // the same body and the same choices[0].message.content — so the cheapest
  // option on the day wins instead of being chosen now, in advance.
  //
  // The ALLOWED_MODELS check above still runs against the model the APP asked
  // for. That is deliberate: it is a cost-control guard on the client, not a
  // statement about the upstream, and LLM_MODEL supplies the real model id.
  //
  // KNOWN DEGRADATION: 3 of the app's 7 call sites pass
  // tools:[{type:'web_search_20250305'}] — Anthropic server-side web search.
  // No OpenAI-compatible equivalent exists in this shape, so tools are DROPPED
  // when the fallback is active. Those reads still generate, without live web
  // lookup. That is a deliberate, documented downgrade rather than a 400.
  // ══════════════════════════════════════════════════════════════════════
  const provider = (Deno.env.get("LLM_PROVIDER") || "anthropic")
    .trim().toLowerCase();
  const isAnthropic = provider === "anthropic";

  // Fallback providers use their own key; Anthropic keeps ANTHROPIC_API_KEY.
  const upstreamKey = isAnthropic
    ? apiKey
    : (Deno.env.get("LLM_API_KEY") || Deno.env.get("OPENAI_API_KEY") || "");
  if (!upstreamKey) {
    console.error(`[claude-proxy] no key for provider=${provider}`);
    return json({ error: "Server not configured" }, 500);
  }

  let upstreamUrl: string;
  let upstreamHeaders: Record<string, string>;
  let upstreamBody: Record<string, unknown>;

  if (isAnthropic) {
    upstreamUrl = "https://api.anthropic.com/v1/messages";
    upstreamHeaders = {
      "Content-Type": "application/json",
      "x-api-key": upstreamKey,
      "anthropic-version": "2023-06-01",
    };
    upstreamBody = { model, max_tokens: maxTokens, messages };
    if (body.system) upstreamBody.system = body.system;
    if (body.tools) upstreamBody.tools = body.tools;
    if (body.temperature !== undefined) upstreamBody.temperature = body.temperature;
    if (body.thinking) upstreamBody.thinking = body.thinking;
    if (body.tool_choice) upstreamBody.tool_choice = body.tool_choice;
  } else {
    upstreamUrl = Deno.env.get("LLM_BASE_URL") ||
      "https://api.openai.com/v1/chat/completions";
    upstreamHeaders = {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${upstreamKey}`,
    };
    // Anthropic puts the system prompt in its own field; chat/completions wants
    // it as the first message.
    const oaiMessages = body.system
      ? [{ role: "system", content: String(body.system) }, ...messages]
      : messages;
    upstreamBody = {
      model: Deno.env.get("LLM_MODEL") || model,
      max_tokens: maxTokens,
      messages: oaiMessages,
    };
    if (body.temperature !== undefined) upstreamBody.temperature = body.temperature;
    if (body.tools) {
      console.warn("[claude-proxy] dropping tools — no equivalent on " + provider);
    }
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), ANTHROPIC_TIMEOUT_MS);

  try {
    const r = await fetch(upstreamUrl, {
      method: "POST",
      headers: upstreamHeaders,
      body: JSON.stringify(upstreamBody),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    const responseText = await r.text();

    // Anthropic: pass through transparently — the app's existing parser handles
    // {content:[{type:'text',text:'...'}]} unchanged.
    if (isAnthropic) {
      return new Response(responseText, {
        status: r.status,
        headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
      });
    }

    // Fallback: translate chat/completions INTO the Anthropic shape, so
    // app/index.tsx's `data.content.filter(b => b.type === 'text')` keeps
    // working with no client change. This translation is the entire reason a
    // provider swap does not need an App Store build.
    let parsed: any = null;
    try { parsed = JSON.parse(responseText); } catch { /* fall through */ }
    if (!r.ok || !parsed) {
      console.error(`[claude-proxy] ${provider} ${r.status}: ${responseText.slice(0, 200)}`);
      return json(
        { error: parsed?.error?.message || `Upstream ${r.status}`, content: [] },
        r.status || 502,
      );
    }
    const text = parsed?.choices?.[0]?.message?.content ?? "";
    return json({
      id: parsed?.id ?? null,
      type: "message",
      role: "assistant",
      model: parsed?.model ?? upstreamBody.model,
      content: text ? [{ type: "text", text: String(text) }] : [],
      stop_reason: parsed?.choices?.[0]?.finish_reason ?? null,
      usage: {
        input_tokens: parsed?.usage?.prompt_tokens ?? null,
        output_tokens: parsed?.usage?.completion_tokens ?? null,
      },
    }, 200);
  } catch (e) {
    clearTimeout(timeoutId);
    if ((e as Error).name === "AbortError") {
      return json({ error: `${provider} API timeout`, content: [] }, 504);
    }
    console.error("[claude-proxy] upstream error:", (e as Error).message);
    return json({ error: "Upstream error", content: [] }, 502);
  }
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
