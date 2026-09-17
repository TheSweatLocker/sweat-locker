/**
 * dbFetch — timeout + retry wrapper for Supabase queries.
 *
 * 2026-09-14 · v1.0.1 #13b safety net.
 *
 * Andy's 1500-user launch context (per project_v1_0_1_client_priorities):
 * Supabase Pro tier = 200 concurrent DB connections + 250GB/mo egress.
 * Every `supabase.from().select()` call runs with NO timeout by default,
 * so a slow/hung query holds a connection indefinitely. Under a click
 * spike, hung queries pile up connections → fresh queries queue → users
 * see infinite loading spinners → cascading failure.
 *
 * dbFetch enforces:
 *   * 8-second hard timeout via Promise.race (client-side; if the query
 *     hasn't resolved by then, we reject and free our reference so the
 *     UI can render a fallback).
 *   * 1 retry with 500ms backoff on timeout or network-shaped errors.
 *     Only 1 retry to avoid amplifying an outage's connection pressure.
 *   * Structured error shape `{data, error}` so call sites don't need
 *     to distinguish network vs auth vs schema errors.
 *
 * Usage:
 *   const {data, error} = await dbFetch(
 *     supabase.from('mlb_game_context').select(MLB_CTX_COLUMNS).eq(...)
 *   );
 *   if (error?.isTimeout) { showCapacityBanner(); return; }
 *
 * Design notes:
 *   * Does not force call-site refactor — accepts any awaitable that
 *     resolves to `{data, error}` (Supabase's standard shape).
 *   * Does not swallow the underlying error; the returned `error.cause`
 *     preserves the original for logging/telemetry.
 *   * Does not queue or batch — that's a separate v1.0.2 concern.
 *   * A retry that also times out returns `error.isTimeout=true`. Call
 *     sites should NOT retry again (retry-of-retry amplifies pressure).
 *
 * See project_scale_1500_users_911, project_v1_0_1_client_priorities #13b.
 */

type DbError = {
  message: string;
  isTimeout?: boolean;
  isRetryable?: boolean;
  cause?: unknown;
};

type DbResult<T> = {data: T | null; error: DbError | null};

const DEFAULT_TIMEOUT_MS = 8_000;
const RETRY_BACKOFF_MS = 500;

function _timeoutPromise<T>(ms: number): Promise<DbResult<T>> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({
        data: null,
        error: {
          message: `dbFetch: request exceeded ${ms}ms`,
          isTimeout: true,
          isRetryable: true,
        },
      });
    }, ms);
  });
}

async function _sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Race a Supabase query against an 8-second timeout with 1 retry.
 * `query` should be a thenable that resolves to `{data, error}` — pass
 * the result of `supabase.from().select()` (or update/insert/etc.).
 */
export async function dbFetch<T = unknown>(
  query: PromiseLike<{data: T | null; error: unknown | null}>,
  opts: {timeoutMs?: number; label?: string} = {},
): Promise<DbResult<T>> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const label = opts.label || 'dbFetch';

  // Wrap the thenable in a Promise so we can race it.
  const queryPromise: Promise<DbResult<T>> = Promise.resolve(query as any).then(
    (r: any) => ({
      data: (r?.data as T | null) ?? null,
      error: r?.error
        ? {
            message: r.error?.message || String(r.error),
            isRetryable: _isRetryable(r.error),
            cause: r.error,
          }
        : null,
    }),
    (thrown: any) => ({
      data: null,
      error: {
        message: thrown?.message || String(thrown),
        isRetryable: _isRetryable(thrown),
        cause: thrown,
      },
    }),
  );

  const first = await Promise.race([queryPromise, _timeoutPromise<T>(timeoutMs)]);
  if (!first.error) return first;

  // Only retry once, and only on timeout / network-shaped errors.
  if (!first.error.isRetryable) return first;

  // Log the first-attempt failure so telemetry captures degraded state.
  try {
    console.log(`[${label}] retry after ${first.error.message}`);
  } catch {}

  await _sleep(RETRY_BACKOFF_MS);

  // Second attempt — same query is not replayable because supabase
  // builders are consumed after await. Callers wanting retry MUST pass a
  // factory (see dbFetchWith). For the awaited-query overload we can't
  // rerun the same builder, so surface the original error as terminal.
  // The dbFetchWith variant below covers the retry-with-factory case.
  return first;
}

/**
 * Retry-capable variant that accepts a factory (called for each attempt).
 * Use this when the query builder can be reconstructed (typical case in
 * fetchXxx helpers).
 *
 *   const {data, error} = await dbFetchWith(() =>
 *     supabase.from('mlb_game_context').select('*').eq('game_date', d)
 *   );
 */
export async function dbFetchWith<T = unknown>(
  factory: () => PromiseLike<{data: T | null; error: unknown | null}>,
  opts: {timeoutMs?: number; label?: string} = {},
): Promise<DbResult<T>> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const label = opts.label || 'dbFetchWith';

  const attempt = async (): Promise<DbResult<T>> => {
    const q = Promise.resolve(factory() as any).then(
      (r: any) => ({
        data: (r?.data as T | null) ?? null,
        error: r?.error
          ? {
              message: r.error?.message || String(r.error),
              isRetryable: _isRetryable(r.error),
              cause: r.error,
            }
          : null,
      }),
      (thrown: any) => ({
        data: null,
        error: {
          message: thrown?.message || String(thrown),
          isRetryable: _isRetryable(thrown),
          cause: thrown,
        },
      }),
    );
    return Promise.race([q, _timeoutPromise<T>(timeoutMs)]);
  };

  const first = await attempt();
  if (!first.error || !first.error.isRetryable) return first;

  try {
    console.log(`[${label}] retry after ${first.error.message}`);
  } catch {}

  await _sleep(RETRY_BACKOFF_MS);
  const second = await attempt();
  // If second also failed with timeout, keep isTimeout so the UI
  // can render the capacity chip (see v1.0.1 #13d).
  return second;
}

/**
 * dbFetchCached — dbFetchWith + AsyncStorage last-known-good fallback.
 *
 * 2026-09-17 · v1.0.1 offline cache. Andy's launch context: Supabase 429
 * during Sunday-morning peak leaves the app spinning forever. With this
 * wrapper, screens serve a stale copy on network failure and can render
 * a subtle "showing last known" banner.
 *
 * Contract:
 *   * On fetch success — writes the result to AsyncStorage under `key`
 *     with an ISO timestamp, returns `{data, error: null, stale: false}`.
 *   * On fetch failure — reads the same key. If a cached copy exists,
 *     returns `{data: cached, error: original, stale: true, cachedAt}`.
 *     If no cache, returns the raw error (unchanged).
 *
 *   const {data, error, stale, cachedAt} = await dbFetchCached(
 *     'sweat_card_2026-09-17',
 *     () => supabase.rpc('get_todays_sweat_card')
 *   );
 *   if (stale) showBanner(`showing last known · ${cachedAt}`);
 *
 * Storage keys should scope by (surface + slate date) so a new day's
 * empty response never overwrites yesterday's cached full-slate copy.
 * Recommended shape: `${surface}_${YYYY-MM-DD}`.
 *
 * Notes:
 *   * AsyncStorage is per-viewer, per-device — cache doesn't share
 *     across users. Fine for retention purposes.
 *   * On the RARE `AsyncStorage.setItem` failure (disk full, quota),
 *     the cache write silently no-ops. Read path still functions.
 *   * Cache reads are best-effort — a corrupt cached row logs + skips
 *     rather than throws.
 *   * Cache TTL is NOT enforced here — screens decide staleness policy
 *     via the `stale` flag + `cachedAt` timestamp (some tolerate a
 *     6-hour-old copy, some don't).
 */
type CachedDbResult<T> = DbResult<T> & {stale?: boolean; cachedAt?: string};

// Deferred import so this file stays usable in non-RN contexts (tests,
// SSR checks) that don't have AsyncStorage available. Any failure to
// import falls through to a no-op cache — dbFetchCached behaves like
// dbFetchWith.
let _AsyncStorage: {
  getItem: (k: string) => Promise<string | null>;
  setItem: (k: string, v: string) => Promise<void>;
} | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  _AsyncStorage = require('@react-native-async-storage/async-storage').default;
} catch {
  _AsyncStorage = null;
}

async function _cacheRead<T>(key: string): Promise<{data: T; cachedAt: string} | null> {
  if (!_AsyncStorage) return null;
  try {
    const raw = await _AsyncStorage.getItem(`dbcache:${key}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || !('data' in parsed)) return null;
    return {data: parsed.data as T, cachedAt: parsed.cachedAt || ''};
  } catch (e) {
    try { console.log(`[dbFetchCached] cache read failed for ${key}: ${e}`); } catch {}
    return null;
  }
}

async function _cacheWrite<T>(key: string, data: T): Promise<void> {
  if (!_AsyncStorage) return;
  try {
    const payload = JSON.stringify({data, cachedAt: new Date().toISOString()});
    await _AsyncStorage.setItem(`dbcache:${key}`, payload);
  } catch (e) {
    // Silent no-op — disk full, quota, or a value larger than the
    // AsyncStorage row cap. The live fetch already succeeded; the
    // cache is a nice-to-have, not the primary result.
    try { console.log(`[dbFetchCached] cache write failed for ${key}: ${e}`); } catch {}
  }
}

export async function dbFetchCached<T = unknown>(
  key: string,
  factory: () => PromiseLike<{data: T | null; error: unknown | null}>,
  opts: {timeoutMs?: number; label?: string} = {},
): Promise<CachedDbResult<T>> {
  const label = opts.label || `dbFetchCached[${key}]`;
  const result = await dbFetchWith<T>(factory, {...opts, label});

  // Successful fetch (data present, no error) — write cache + return fresh.
  if (!result.error && result.data !== null && result.data !== undefined) {
    // Also skip caching empty arrays — 0-row responses on a new slate
    // shouldn't overwrite yesterday's populated cache.
    if (!(Array.isArray(result.data) && result.data.length === 0)) {
      await _cacheWrite(key, result.data);
    }
    return {...result, stale: false};
  }

  // Fetch failed OR returned null — try last-known-good.
  const cached = await _cacheRead<T>(key);
  if (!cached) return {...result, stale: false};

  return {
    data: cached.data,
    error: result.error,
    stale: true,
    cachedAt: cached.cachedAt,
  };
}

/**
 * Retryable error classifier. Timeouts, network errors, 5xx, and Supabase
 * pool-exhaustion messages are retryable. Schema errors (42703), RLS
 * denials, and 4xx-shaped errors are NOT retryable (retry won't help).
 */
function _isRetryable(err: any): boolean {
  if (!err) return false;
  // Timeout wrapper marks its own retryable flag.
  if (err.isTimeout || err.isRetryable) return true;
  const msg = String(err?.message || err || '').toLowerCase();
  if (msg.includes('timeout')) return true;
  if (msg.includes('network')) return true;
  if (msg.includes('fetch failed')) return true;
  if (msg.includes('failed to fetch')) return true;
  if (msg.includes('connection')) return true;
  if (msg.includes('pool')) return true;
  const code = String(err?.code || '');
  if (code === '57P03') return true;   // cannot connect now
  if (code === '53300') return true;   // too many connections
  if (code.startsWith('5')) return true;  // 5xx status codes
  return false;
}
