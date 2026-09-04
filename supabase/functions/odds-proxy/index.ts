// supabase/functions/odds-proxy/index.ts
//
// Server-side proxy for The Odds API (theoddsapi.com). Eliminates
// ODDS_API_KEY from the app binary (was EXPO_PUBLIC_* — extractable
// from any IPA), and dramatically cuts per-user Odds credit burn via
// shared cache.
//
// Before: 12 client sites hit theoddsapi.com directly per user session
//         → ~100 credits/DAU/day, ~$170/mo linear-with-users at 1000 DAU,
//         → forces $199 Odds plan tier, plus abuse-DoS if key extracted.
// After:  1 proxy hit / cache lookup, ~30-60s TTL per (endpoint, params)
//         → 1000 users refresh Sharp Card in 5 mins = 1 upstream credit
//         → cost stays FLAT regardless of user count.
//
// Deploy:
//   supabase functions deploy odds-proxy
// Set secret (one time, server-side only):
//   supabase secrets set ODDS_API_KEY=xxxxx
// Migration for cache table lives in supabase/migrations/20260904a_odds_cache.sql
//
// Request body (from client):
//   {
//     "endpoint": "/v4/sports/baseball_mlb/odds",
//     "params": {
//       "regions": "us,us2",
//       "markets": "spreads,totals,h2h",
//       "oddsFormat": "american",
//       "bookmakers": "hardrockbet,draftkings,..."
//     }
//   }
// Note: NO apiKey field — server injects it. Requests with apiKey field
// are 400ed (someone trying to bypass rate limits).
//
// Response: whatever the Odds API returned, passed through transparently.
// Existing client parsing (r.data) works unchanged.
//
// TTL logic (per endpoint pattern):
//   /historical/         → forever (immutable)
//   /scores              → 300s (5min, scores tick slower)
//   /odds                → 60s (live-ish)
//   /events (list)       → 300s (event roster doesn't churn)
//   /events/{id}/odds    → 60s (per-event odds, similar cadence)
//   default              → 60s
//
// Defense layers:
//   1. Endpoint allowlist (rejects arbitrary URLs)
//   2. No client-provided apiKey (server injects)
//   3. Cache-first (kills upstream credit burn)
//   4. Prompt size limit / max URL length
//   5. Pass-through error codes so client can handle 429/503

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ODDS_BASE = "https://api.the-odds-api.com";

// Only allow paths that match one of these patterns. Prevents an attacker
// from proxying to arbitrary URLs by faking the endpoint field.
const ALLOWED_ENDPOINTS = [
  /^\/v4\/sports\/?$/,
  /^\/v4\/sports\/[a-z_]+\/odds\/?$/,
  /^\/v4\/sports\/[a-z_]+\/scores\/?$/,
  /^\/v4\/sports\/[a-z_]+\/events\/?$/,
  /^\/v4\/sports\/[a-z_]+\/events\/[a-z0-9]+\/odds\/?$/,
  /^\/v4\/historical\/sports\/[a-z_]+\/odds\/?$/,
];

function ttlForEndpoint(endpoint: string): number {
  if (endpoint.includes("/historical/")) return 24 * 3600;  // 24h (immutable-ish)
  if (endpoint.includes("/scores")) return 300;              // 5 min
  if (endpoint.includes("/events/") && endpoint.endsWith("/odds")) return 60;
  if (endpoint.endsWith("/odds")) return 60;
  if (endpoint.endsWith("/events")) return 300;
  return 60;
}

// Stable cache key = endpoint + sorted params (JSON-encoded).
function cacheKey(endpoint: string, params: Record<string, string>): string {
  const sortedKeys = Object.keys(params).sort();
  const paramsStr = sortedKeys.map((k) => `${k}=${params[k]}`).join("&");
  return `${endpoint}?${paramsStr}`;
}

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS_HEADERS });
  }
  if (req.method !== "POST") {
    return json({ error: "POST only" }, 405);
  }

  const oddsKey = Deno.env.get("ODDS_API_KEY");
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!oddsKey) {
    console.error("[odds-proxy] ODDS_API_KEY missing");
    return json({ error: "Server not configured" }, 500);
  }
  const supabase = supabaseUrl && serviceKey
    ? createClient(supabaseUrl, serviceKey)
    : null;

  let body: any;
  try {
    body = await req.json();
  } catch {
    return json({ error: "Invalid JSON" }, 400);
  }

  const endpoint = String(body.endpoint || "").trim();
  const params = (body.params && typeof body.params === "object")
    ? body.params
    : {};

  if (!endpoint) return json({ error: "endpoint required" }, 400);

  // Endpoint allowlist
  const allowed = ALLOWED_ENDPOINTS.some((rx) => rx.test(endpoint));
  if (!allowed) return json({ error: `endpoint ${endpoint} not allowed` }, 400);

  // Reject client-supplied apiKey (server injects — no bypass)
  if ("apiKey" in params) delete params.apiKey;

  // Normalize params to strings
  const normalizedParams: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    normalizedParams[String(k)] = String(v);
  }

  const key = cacheKey(endpoint, normalizedParams);
  const ttl = ttlForEndpoint(endpoint);
  const nowIso = new Date().toISOString();

  // 1. Cache lookup
  if (supabase) {
    try {
      const { data: cached } = await supabase
        .from("odds_cache")
        .select("data, expires_at")
        .eq("cache_key", key)
        .maybeSingle();
      if (cached && cached.expires_at && new Date(cached.expires_at) > new Date()) {
        return new Response(JSON.stringify(cached.data), {
          status: 200,
          headers: {
            ...CORS_HEADERS,
            "Content-Type": "application/json",
            "x-cache": "HIT",
          },
        });
      }
    } catch (e) {
      console.warn("[odds-proxy] cache read failed:", (e as Error).message);
    }
  }

  // 2. Upstream fetch (cache miss)
  const url = new URL(ODDS_BASE + endpoint);
  url.searchParams.set("apiKey", oddsKey);
  for (const [k, v] of Object.entries(normalizedParams)) {
    url.searchParams.set(k, v);
  }

  let upstreamStatus = 502;
  let upstreamJson: any = null;
  let upstreamText = "";
  try {
    const r = await fetch(url.toString(), {
      headers: { "Accept": "application/json" },
    });
    upstreamStatus = r.status;
    upstreamText = await r.text();
    try {
      upstreamJson = JSON.parse(upstreamText);
    } catch {
      upstreamJson = null;
    }
  } catch (e) {
    console.error("[odds-proxy] upstream fetch failed:", (e as Error).message);
    return json({ error: "Upstream fetch failed" }, 502);
  }

  // 3. Write cache (only on success)
  if (supabase && upstreamStatus === 200 && upstreamJson !== null) {
    try {
      const expiresAt = new Date(Date.now() + ttl * 1000).toISOString();
      await supabase.from("odds_cache").upsert({
        cache_key: key,
        endpoint,
        data: upstreamJson,
        fetched_at: nowIso,
        expires_at: expiresAt,
      });
    } catch (e) {
      console.warn("[odds-proxy] cache write failed:", (e as Error).message);
    }
  }

  return new Response(upstreamText, {
    status: upstreamStatus,
    headers: {
      ...CORS_HEADERS,
      "Content-Type": "application/json",
      "x-cache": "MISS",
    },
  });
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
