/**
 * HomeStreakBanner — server-controlled rotating banner on Home tab.
 *
 * 2026-09-17 v2 (per Andy directive): moved from client-side auto-
 * computed banners + hardcoded templates → pure server-driven reads
 * from public.home_banners. Adds/removes banner classes is now a SQL
 * INSERT/UPDATE, no client rebuild.
 *
 * Data flow:
 *   * Server-side cron `mlb_pipeline/compute_home_banners.py` (follow-up)
 *     recomputes auto-banners from surface_records post-pipeline. Uses
 *     ON CONFLICT (kind, origin) DO UPDATE so each auto-banner class
 *     has one live row at a time.
 *   * Admin manual pushes: INSERT INTO home_banners (icon, message,
 *     deep_link, sport, route, priority, expires_at, origin='admin').
 *   * Client reads all live rows (expires_at IS NULL OR expires_at > NOW),
 *     filters by currentSport + route='home' (or null), sorts by
 *     priority DESC, takes top 3, rotates every 8s.
 *
 * Silent-hide when no active rows — no filler.
 *
 * See supabase/migrations/20260917d_home_banners.sql for schema +
 * seed row. Copy any live SQL update onto the row for changes.
 */
import React from 'react';
import {View, Text, TouchableOpacity, StyleSheet} from 'react-native';
import {THEME} from '../theme';

type Banner = {
  id: number;
  icon: string;
  message: string;
  deep_link?: string | null;
  sport?: string | null;
  route?: string | null;
  priority: number;
  starts_at?: string | null;
  expires_at?: string | null;
  origin?: string;
  kind?: string | null;
};

type Props = {
  supabase: any;
  /** Current sport tab (from parent). Banners with sport=null show
   *  always; banners with a specific sport only show when matched. */
  currentSport?: string;
  /** Which screen is rendering the banner. Server rows with
   *  route='home' or route=null show here; server rows scoped to
   *  another route silent-hide. */
  currentRoute?: string;
  /** Poll interval (ms). Default 5 min — matches AdminNoticeBanner
   *  cadence. Cron runs post-pipeline so 5-min catch-up is fine. */
  pollIntervalMs?: number;

  // Deep-link handlers — server rows include a deep_link string; the
  // component maps that string to the parent's tab-switch callbacks.
  onOpenSharp?: () => void;
  onOpenLadder?: () => void;
  onOpenLedger?: () => void;
  onOpenDailyDegen?: () => void;
  onOpenJerry?: () => void;
};

const _resolveDeepLink = (
  key: string | null | undefined,
  h: Pick<Props, 'onOpenSharp' | 'onOpenLadder' | 'onOpenLedger' | 'onOpenDailyDegen' | 'onOpenJerry'>,
): (() => void) | undefined => {
  switch ((key || '').toLowerCase()) {
    case 'sharp':       return h.onOpenSharp;
    case 'ladder':      return h.onOpenLadder;
    case 'ledger':      return h.onOpenLedger;
    case 'daily_degen': return h.onOpenDailyDegen;
    case 'jerry':       return h.onOpenJerry;
    default:            return undefined;
  }
};

export function HomeStreakBanner({
  supabase,
  currentSport,
  currentRoute = 'home',
  pollIntervalMs = 300_000,
  onOpenSharp,
  onOpenLadder,
  onOpenLedger,
  onOpenDailyDegen,
  onOpenJerry,
}: Props) {
  const [banners, setBanners] = React.useState<Banner[] | null>(null);
  const [idx, setIdx] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;

    async function poll() {
      if (!supabase) return;
      try {
        const nowIso = new Date().toISOString();
        const {data, error} = await supabase
          .from('home_banners')
          .select('id,icon,message,deep_link,sport,route,priority,starts_at,expires_at,origin,kind')
          .lte('starts_at', nowIso)
          .or(`expires_at.is.null,expires_at.gt.${nowIso}`)
          .order('priority', {ascending: false})
          .limit(20);
        if (cancelled) return;
        if (error) { setBanners([]); return; }
        // Client-side scope filter — sport + route null means "any"
        const scoped = (data || []).filter((r: Banner) => {
          if (r.sport && currentSport && r.sport.toUpperCase() !== currentSport.toUpperCase()) return false;
          if (r.route && currentRoute && r.route.toLowerCase() !== currentRoute.toLowerCase()) return false;
          return true;
        });
        // Sort was server-side but re-sort defensively (in case server rows shifted mid-fetch)
        scoped.sort((a: Banner, b: Banner) => b.priority - a.priority);
        setBanners(scoped.slice(0, 3));
        setIdx(0);
      } catch {
        if (!cancelled) setBanners([]);
      }
    }

    poll();
    const t = setInterval(poll, pollIntervalMs);
    return () => { cancelled = true; clearInterval(t); };
  }, [supabase, currentSport, currentRoute, pollIntervalMs]);

  // Rotate every 8s if 2+ banners qualify. Same UX as prior client-
  // computed HomeStreakBanner.
  React.useEffect(() => {
    if (!banners || banners.length <= 1) return;
    const t = setInterval(() => {
      setIdx((i) => (i + 1) % banners.length);
    }, 8000);
    return () => clearInterval(t);
  }, [banners]);

  if (!banners || banners.length === 0) return null;
  const b = banners[idx % banners.length];
  const onPress = _resolveDeepLink(b.deep_link, {
    onOpenSharp, onOpenLadder, onOpenLedger, onOpenDailyDegen, onOpenJerry,
  });

  const inner = (
    <View style={styles.wrap}>
      <Text style={styles.icon}>{b.icon || '🔥'}</Text>
      <Text style={styles.text} numberOfLines={2}>{b.message}</Text>
    </View>
  );
  return onPress ? (
    <TouchableOpacity onPress={onPress} activeOpacity={0.8}>{inner}</TouchableOpacity>
  ) : inner;
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginBottom: 8,
    borderRadius: 8,
    backgroundColor: THEME.accent + '18',
    borderLeftWidth: 3,
    borderLeftColor: THEME.accent,
  },
  icon: {
    fontSize: 15,
  },
  text: {
    flex: 1,
    fontSize: 12,
    fontWeight: '700',
    color: THEME.accent,
    letterSpacing: 0.2,
  },
});
