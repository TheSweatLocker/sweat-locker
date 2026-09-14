/**
 * withCache — thin TTL cache for stable client reads.
 *
 * 2026-09-14 · v1.0.1 #13c.
 *
 * Andy's 1500-user launch: stable reads (games list, sharp_card, potd,
 * receipts, ledger, jerry_reads) are fetched on every screen mount +
 * every pull-to-refresh. Nothing dedupes hits inside a 5-minute window.
 * On peak-hour click spikes this multiplies base egress by ~10× per user
 * even though the underlying data hasn't changed.
 *
 * withCache wraps any fetcher with:
 *   * In-memory cache (per-session, ~zero-cost hits)
 *   * AsyncStorage-backed persistent cache (survives app cold start)
 *   * TTL-based invalidation (default 5 min)
 *   * Stale-while-revalidate: on cache hit, return cached data
 *     immediately AND kick off a background refresh so the next hit
 *     within the TTL is fresh. Optional (opt-in).
 *
 * Usage:
 *   const games = await withCache(
 *     'games_MLB_today',
 *     () => fetchGamesFromServer('MLB', 'today'),
 *     {ttlMs: 5*60_000}
 *   );
 *
 * Invalidation:
 *   invalidateCache('games_MLB_today')  // explicit bust
 *   invalidateCachePrefix('games_')     // wildcard bust
 *
 * Not used for per-user private data (Receipts detail rows, personal
 * bets) — those need cache scoped to auth uid, which the current app
 * doesn't have; that's a v1.0.2 concern.
 *
 * See project_scale_1500_users_911, project_v1_0_1_client_priorities #13c.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

type CacheEntry<T> = {data: T; ts: number};

const CACHE_KEY_PREFIX = 'sweatlocker_cache_';
const DEFAULT_TTL_MS = 5 * 60 * 1000;

// Per-session in-memory cache. Cleared on app cold start.
const _memory: Map<string, CacheEntry<unknown>> = new Map();

// Track in-flight fetches so parallel callers dedupe onto one request.
const _inflight: Map<string, Promise<unknown>> = new Map();

function _storageKey(key: string): string {
  return CACHE_KEY_PREFIX + key;
}

async function _readPersistent<T>(key: string): Promise<CacheEntry<T> | null> {
  try {
    const raw = await AsyncStorage.getItem(_storageKey(key));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || typeof parsed.ts !== 'number') {
      return null;
    }
    return parsed as CacheEntry<T>;
  } catch {
    return null;
  }
}

async function _writePersistent<T>(key: string, entry: CacheEntry<T>): Promise<void> {
  try {
    await AsyncStorage.setItem(_storageKey(key), JSON.stringify(entry));
  } catch {
    // Storage-full or malformed data — silent skip. In-memory cache
    // still holds the value for the session.
  }
}

function _fresh<T>(entry: CacheEntry<T> | null, ttlMs: number): boolean {
  return !!entry && (Date.now() - entry.ts) < ttlMs;
}

/**
 * Return cached value if fresh; otherwise call `fetcher`, cache, return.
 * Parallel calls with the same key dedupe onto the same in-flight fetch.
 *
 * opts.staleWhileRevalidate = true: on stale hit within 2× TTL, return
 * the stale value AND kick off a background refresh. Callers get
 * instant response; next call inside the fresh window sees new data.
 */
export async function withCache<T>(
  key: string,
  fetcher: () => Promise<T>,
  opts: {ttlMs?: number; staleWhileRevalidate?: boolean} = {},
): Promise<T> {
  const ttlMs = opts.ttlMs ?? DEFAULT_TTL_MS;
  const swr = !!opts.staleWhileRevalidate;

  // 1. In-memory hit.
  const mem = _memory.get(key) as CacheEntry<T> | undefined;
  if (mem && _fresh(mem, ttlMs)) return mem.data;

  // 2. Persistent hit (survives cold start).
  const persisted = await _readPersistent<T>(key);
  if (persisted && _fresh(persisted, ttlMs)) {
    _memory.set(key, persisted);
    return persisted.data;
  }

  // 3. Stale-while-revalidate: return stale value + refetch in background.
  if (swr && persisted && (Date.now() - persisted.ts) < ttlMs * 2) {
    _memory.set(key, persisted);
    // Background refresh — don't await, don't surface errors here.
    _fetchAndCache(key, fetcher).catch(() => {});
    return persisted.data;
  }

  // 4. Miss — fetch, cache, return. Dedupe parallel callers.
  return _fetchAndCache(key, fetcher);
}

async function _fetchAndCache<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const existing = _inflight.get(key) as Promise<T> | undefined;
  if (existing) return existing;

  const promise = (async () => {
    try {
      const data = await fetcher();
      const entry: CacheEntry<T> = {data, ts: Date.now()};
      _memory.set(key, entry);
      // Persist in background — don't block the caller.
      _writePersistent(key, entry).catch(() => {});
      return data;
    } finally {
      _inflight.delete(key);
    }
  })();

  _inflight.set(key, promise);
  return promise;
}

/**
 * Bust a specific cache key. In-memory + persistent both cleared. Use
 * this after user actions that should force a refresh (pull-to-refresh,
 * post-write invalidation, sport switch).
 */
export async function invalidateCache(key: string): Promise<void> {
  _memory.delete(key);
  try {
    await AsyncStorage.removeItem(_storageKey(key));
  } catch {}
}

/**
 * Bust every cache key that starts with `prefix`. Handy for cross-sport
 * invalidation (e.g. `invalidateCachePrefix('games_')` on manual refresh).
 */
export async function invalidateCachePrefix(prefix: string): Promise<void> {
  const memKeys: string[] = [];
  _memory.forEach((_v, k) => {
    if (k.startsWith(prefix)) memKeys.push(k);
  });
  for (const k of memKeys) _memory.delete(k);
  try {
    const allKeys = await AsyncStorage.getAllKeys();
    const storagePrefix = CACHE_KEY_PREFIX + prefix;
    const toRemove = allKeys.filter((k) => k.startsWith(storagePrefix));
    if (toRemove.length) await AsyncStorage.multiRemove(toRemove);
  } catch {}
}

/**
 * Read-through peek: returns cached value if any (regardless of TTL) or
 * null. Useful for "show something instantly, then refresh" render paths
 * where the caller drives their own refresh policy.
 */
export async function peekCache<T>(key: string): Promise<T | null> {
  const mem = _memory.get(key) as CacheEntry<T> | undefined;
  if (mem) return mem.data;
  const persisted = await _readPersistent<T>(key);
  return persisted?.data ?? null;
}
