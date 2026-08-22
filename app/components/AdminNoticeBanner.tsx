/**
 * AdminNoticeBanner — invisible-when-empty in-app banner for live ops notes.
 *
 * Reads from public.admin_notice (see 20260821_admin_notice.sql). Renders
 * the top active notice at the top of the screen. If no rows are active in
 * the window, renders nothing (component is fully invisible / occupies zero
 * space so screens don't need to reserve room for it).
 *
 * Use cases:
 *   - "Sharp Card locking early tonight — awaiting late line moves"
 *   - "Stale odds visible for 5 min while book refresh runs"
 *   - "Investigating grader delay — resolved picks show tomorrow AM"
 *
 * Set via Supabase console:
 *   INSERT INTO admin_notice (message, severity, expires_at) VALUES
 *     ('...', 'warning', NOW() + INTERVAL '2 hours');
 *
 * Dismiss:
 *   UPDATE admin_notice SET expires_at = NOW() WHERE id = X;
 *
 * Local dismiss (client-side, dismissible=true): tapping X hides for this
 * session only — reopens on next app cold-start unless the DB row is
 * cleared.
 */
import React, { useEffect, useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { THEME } from '../theme';

type Severity = 'info' | 'warning' | 'critical';

type Notice = {
  id: number;
  message: string;
  severity: Severity;
  dismissible: boolean;
  sport?: string | null;
  route?: string | null;
};

type Props = {
  supabase: any;                 // pass in from parent (avoids duplicate client)
  currentSport?: string;         // optional filter — hides sport-scoped notices for other sports
  currentRoute?: string;         // optional filter — hides route-scoped notices for other routes
  pollIntervalMs?: number;       // default 60000 (60s)
};

const SEVERITY_STYLE: Record<Severity, { bg: string; fg: string }> = {
  info:     { bg: THEME.sharp,   fg: THEME.ink },
  warning:  { bg: THEME.warn,    fg: THEME.ink },
  critical: { bg: THEME.loss,    fg: '#FFFFFF' },
};

export default function AdminNoticeBanner({
  supabase,
  currentSport,
  currentRoute,
  pollIntervalMs = 60_000,
}: Props) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [dismissedIds, setDismissedIds] = useState<Set<number>>(new Set());

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const nowIso = new Date().toISOString();
        const { data, error } = await supabase
          .from('admin_notice')
          .select('id,message,severity,dismissible,sport,route,starts_at,expires_at')
          .lte('starts_at', nowIso)
          .or(`expires_at.is.null,expires_at.gt.${nowIso}`)
          .order('severity', { ascending: false })   // critical > warning > info alpha; workable
          .order('id', { ascending: false })
          .limit(5);
        if (cancelled || error) return;
        const scoped = (data || []).filter((n: Notice) => {
          if (dismissedIds.has(n.id)) return false;
          if (n.sport && currentSport && n.sport !== currentSport) return false;
          if (n.route && currentRoute && n.route !== currentRoute) return false;
          return true;
        });
        // Show highest-severity survivor
        const rank: Record<Severity, number> = { critical: 3, warning: 2, info: 1 };
        scoped.sort((a: Notice, b: Notice) => rank[b.severity] - rank[a.severity]);
        setNotice(scoped[0] || null);
      } catch (_) {
        // silent fail — banner is best-effort
      }
    }

    poll();
    const t = setInterval(poll, pollIntervalMs);
    return () => { cancelled = true; clearInterval(t); };
  }, [supabase, currentSport, currentRoute, pollIntervalMs, dismissedIds]);

  if (!notice) return null;
  const s = SEVERITY_STYLE[notice.severity] || SEVERITY_STYLE.info;

  return (
    <View style={[styles.banner, { backgroundColor: s.bg }]}>
      <Text style={[styles.msg, { color: s.fg }]} numberOfLines={3}>
        {notice.message}
      </Text>
      {notice.dismissible && (
        <TouchableOpacity
          onPress={() => setDismissedIds(prev => new Set(prev).add(notice.id))}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          style={styles.close}
        >
          <Text style={[styles.closeText, { color: s.fg }]}>✕</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
    gap: 8,
  },
  msg: {
    flex: 1,
    fontSize: 13,
    fontWeight: '600',
  },
  close: {
    width: 24,
    height: 24,
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeText: {
    fontSize: 16,
    fontWeight: '700',
  },
});
