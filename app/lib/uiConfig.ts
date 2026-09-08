/**
 * uiConfig — server-controlled render manifest client (2026-09-08).
 *
 * Lets us hide/rename/reorder UI sections without a binary submission by
 * reading `config_ui_sections` at app cold-start + on interval.
 *
 * Origin: project_ui_toggle_infrastructure_908 memory. Team Matchup vs
 * Team Stats redundancy on NFL Game Detail (noticed 9/8 post-submit) is
 * the first example that made this necessary.
 *
 * Usage pattern (in components):
 *
 *   import { useSectionEnabled } from '@/app/lib/uiConfig';
 *   ...
 *   const showTeamMatchup = useSectionEnabled('NFL', 'game_detail', 'team_matchup');
 *   if (!showTeamMatchup) return null;
 *   return <Section title="Team Matchup" ...>...</Section>;
 *
 * The cache preloads on first call and refreshes every 5 min. Async fetch
 * happens in background — the sync hook returns `defaultVal` (true) until
 * the first fetch resolves, then re-renders with the real value. This
 * avoids "flicker to blank then show section" on cold start.
 */
import { useEffect, useState } from 'react';
import { createClient } from '@supabase/supabase-js';

// Reuse app-wide Supabase client via env; matches the pattern in
// app/components/GameDetailV2.tsx to avoid a second client instance.
const _supabase = createClient(
  process.env.EXPO_PUBLIC_SUPABASE_URL!,
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY!
);

type ConfigRow = {
  sport: string;
  surface: string;
  section_key: string;
  enabled: boolean;
  label_override: string | null;
  hint_override: string | null;
};

type CacheEntry = {
  rows: ConfigRow[];
  loadedAt: number;
};

const TTL_MS = 5 * 60 * 1000; // 5 min
let _cache: CacheEntry | null = null;
let _inflight: Promise<void> | null = null;
const _listeners = new Set<() => void>();

async function _fetchConfig(): Promise<void> {
  try {
    const { data, error } = await _supabase
      .from('config_ui_sections')
      .select('sport,surface,section_key,enabled,label_override,hint_override');
    if (error) {
      console.warn('[uiConfig] fetch error:', error.message);
      return;
    }
    _cache = { rows: (data as ConfigRow[]) || [], loadedAt: Date.now() };
    // Notify all subscribed hooks so components re-render with fresh values
    _listeners.forEach(cb => cb());
  } catch (e) {
    console.warn('[uiConfig] fetch failed:', (e as Error)?.message);
  }
}

function _ensureLoaded(): void {
  const now = Date.now();
  if (_cache && now - _cache.loadedAt < TTL_MS) return;
  if (_inflight) return;
  _inflight = _fetchConfig().finally(() => { _inflight = null; });
}

/**
 * Sync lookup for a section's enabled flag. If cache is warm returns the
 * real value; otherwise triggers a background load and returns defaultVal.
 * Sport-specific rows override 'ALL' rows.
 */
export function isSectionEnabled(
  sport: string,
  surface: string,
  sectionKey: string,
  defaultVal: boolean = true
): boolean {
  _ensureLoaded();
  if (!_cache) return defaultVal;
  const upSport = sport.toUpperCase();
  const upSurface = surface.toLowerCase();
  // Prefer sport-specific match
  const specific = _cache.rows.find(
    r => r.sport === upSport && r.surface === upSurface && r.section_key === sectionKey
  );
  if (specific) return specific.enabled;
  // Fallback to ALL row
  const generic = _cache.rows.find(
    r => r.sport === 'ALL' && r.surface === upSurface && r.section_key === sectionKey
  );
  if (generic) return generic.enabled;
  return defaultVal;
}

/**
 * Optional label/hint overrides. Returns null if no override configured.
 */
export function getSectionLabel(sport: string, surface: string, sectionKey: string): string | null {
  _ensureLoaded();
  if (!_cache) return null;
  const upSport = sport.toUpperCase();
  const upSurface = surface.toLowerCase();
  const row = _cache.rows.find(
    r => (r.sport === upSport || r.sport === 'ALL') &&
         r.surface === upSurface && r.section_key === sectionKey
  );
  return row?.label_override || null;
}

export function getSectionHint(sport: string, surface: string, sectionKey: string): string | null {
  _ensureLoaded();
  if (!_cache) return null;
  const upSport = sport.toUpperCase();
  const upSurface = surface.toLowerCase();
  const row = _cache.rows.find(
    r => (r.sport === upSport || r.sport === 'ALL') &&
         r.surface === upSurface && r.section_key === sectionKey
  );
  return row?.hint_override || null;
}

/**
 * React hook — returns current enabled flag + subscribes for updates when
 * the background fetch completes or the TTL refresh fires. Use this in
 * components so they auto-render when config changes.
 */
export function useSectionEnabled(
  sport: string,
  surface: string,
  sectionKey: string,
  defaultVal: boolean = true
): boolean {
  const [value, setValue] = useState(() =>
    isSectionEnabled(sport, surface, sectionKey, defaultVal)
  );
  useEffect(() => {
    const cb = () => setValue(isSectionEnabled(sport, surface, sectionKey, defaultVal));
    _listeners.add(cb);
    // Trigger a fresh load if cache is stale
    _ensureLoaded();
    return () => { _listeners.delete(cb); };
  }, [sport, surface, sectionKey, defaultVal]);
  return value;
}

/**
 * Force a refresh (e.g. after an admin knob is flipped and we want the
 * change to reflect immediately without waiting for TTL). Rarely needed —
 * the 5-min TTL is usually enough.
 */
export async function refreshUIConfig(): Promise<void> {
  _cache = null;
  _inflight = null;
  await _fetchConfig();
}
