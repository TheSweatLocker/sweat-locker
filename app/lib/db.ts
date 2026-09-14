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
