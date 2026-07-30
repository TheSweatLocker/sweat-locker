/**
 * CohortDashboard — sport-aware live audit cohort table.
 *
 * Surfaces the cohort-level hit rates we already track behind the scenes
 * (NRFI 90-94 sweet spot, confluence_extreme, k_under_strong, spread_delta_ge2, etc.)
 * as user-facing transparency.
 *
 * Reads from mlb_tier_calibration (despite the name, it's multi-sport via
 * the `sport` column added in migration 20260504). Filters to TODAY's
 * computed_date to avoid the truncation bug that was fixed for the
 * sweat card payload — same query pattern.
 *
 * Sport-universal from day one:
 *   - MLB: shows NRFI / confluence / prop-tier / spread_delta cohorts
 *   - NCAAB (Nov+): will show its own cohorts when they ship
 *   - NBA/NFL/NHL (v1.x): same
 *
 * Each cohort row shows: pretty name + window (7d/30d/lifetime) + W-L + n + pct.
 * Pretty name comes from the COHORT_LABELS map so the table reads
 * cleanly to users rather than exposing raw column names.
 */
import React, { useEffect, useState } from 'react';
import { View, Text, ActivityIndicator, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { createClient } from '@supabase/supabase-js';
import { Sport, sportDb, isSportLive } from '../lib/sportPeriods';

import { THEME, TIER_COLOR, OUTCOME_COLOR } from '../theme';
const supabase = createClient(
  process.env.EXPO_PUBLIC_SUPABASE_URL!,
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY!,
);

const BRAND_GREEN = THEME.win;
const BRAND_AMBER = THEME.hrb;
const RED = THEME.loss;
const CARD_BG = THEME.surface;
const TEXT_PRIMARY = THEME.text;
const TEXT_MUTED = THEME.textDim;
const BORDER = THEME.border;

type CohortRow = {
  tier: string;
  window_label: string;
  hits: number;
  total: number;
  hit_rate: number;
};

type CohortDisplayConfig = {
  tier: string;
  label: string;
  description: string | null;
  group_name: string;
  group_order: number;
  display_order: number;
};

// Backend-driven cohort labels (2026-05-23). Reads from
// cohort_display_config table — see migration 20260523_cohort_display_config.sql.
// Adding a new cohort no longer requires an app rebuild: insert a row in
// the table and it surfaces here on next reload. Tiers without a row are
// intentionally hidden (internal/experimental cohorts not ready for UX).
//
// We keep a tiny fallback map in case the network is offline at first
// mount — covers the most critical NRFI + confluence cohorts so the
// dashboard isn't empty for cold-start users. Backend rows always win.
const FALLBACK_LABELS: Record<string, { label: string; group: string; desc?: string }> = {
  nrfi_prime_90_94:        { label: 'NRFI 90-94 (sweet spot)', group: 'NRFI' },
  confluence_prime_ge4:    { label: 'Confluence ≥+4 (PRIME)',    group: 'Signal Stack' },
  confluence_strong_2_3:   { label: 'Confluence 2-3 (STRONG)',   group: 'Signal Stack' },
};

type Props = {
  /** Initial sport — user can switch via in-card chip selector. Defaults to MLB. */
  sport?: Sport;
  /** When true, hide sample size (n) — for free-tier paywall gating */
  hideSamples?: boolean;
};

// Sports we expose in the receipts cohort selector. Order = chip order.
// `disabledLabel` appears when the sport is offseason (isSportLive false).
const SPORTS_AVAILABLE: { sport: Sport; label: string; disabledLabel: string }[] = [
  { sport: 'MLB', label: 'MLB', disabledLabel: 'Returns Mar' },
  { sport: 'NBA', label: 'NBA', disabledLabel: 'Returns Oct' },
  { sport: 'NCAAB', label: 'NCAAB', disabledLabel: 'Returns Nov' },
  { sport: 'NFL', label: 'NFL', disabledLabel: 'Returns Sep' },
];

export const CohortDashboard: React.FC<Props> = ({ sport: initialSport = 'MLB', hideSamples = false }) => {
  const [sport, setSport] = useState<Sport>(initialSport);
  const [rows, setRows] = useState<CohortRow[]>([]);
  const [labels, setLabels] = useState<Record<string, CohortDisplayConfig>>({});
  const [groupOrder, setGroupOrder] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [window, setWindow] = useState<'7d' | '30d' | 'std'>('30d');

  // Fetch backend-driven labels once on mount, refresh on sport change.
  // (Migration: 20260523_cohort_display_config.sql.) If the fetch fails,
  // FALLBACK_LABELS provides minimal coverage so the dashboard isn't empty.
  useEffect(() => {
    fetchLabels();
  }, [sport]);

  useEffect(() => {
    if (!isSportLive(sport)) {
      setLoading(false);
      setRows([]);
      return;
    }
    fetchCohorts();
  }, [sport, window]);

  const fetchLabels = async () => {
    try {
      const { data, error } = await supabase
        .from('cohort_display_config')
        .select('tier, label, description, group_name, group_order, display_order, sport')
        .eq('is_active', true)
        .or(`sport.eq.${sportDb(sport)},sport.is.null`);
      if (error || !data) {
        console.warn('[CohortDashboard] label fetch failed:', error?.message);
        return;
      }
      const labelMap: Record<string, CohortDisplayConfig> = {};
      const groupOrderMap = new Map<string, number>();
      for (const row of data) {
        labelMap[row.tier] = row;
        if (!groupOrderMap.has(row.group_name) || (groupOrderMap.get(row.group_name) ?? 99) > row.group_order) {
          groupOrderMap.set(row.group_name, row.group_order);
        }
      }
      const orderedGroups = Array.from(groupOrderMap.entries())
        .sort((a, b) => a[1] - b[1])
        .map(([name]) => name);
      setLabels(labelMap);
      setGroupOrder(orderedGroups);
    } catch (e: any) {
      console.warn('[CohortDashboard] label fetch exception:', e?.message);
    }
  };

  const fetchCohorts = async () => {
    setLoading(true);
    try {
      // Same truncation defense as the sweat card fix — filter to today's
      // computed_date so we don't hit PostgREST's 1000-row default.
      const today = new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString().slice(0, 10);
      let { data, error } = await supabase
        .from('mlb_tier_calibration')
        .select('tier, window_label, hits, total, hit_rate, computed_date')
        .eq('sport', sportDb(sport))
        .eq('window_label', window)
        .eq('computed_date', today);

      // Fallback to most recent rows if today's batch hasn't computed yet
      if (!data || data.length === 0) {
        const fallback = await supabase
          .from('mlb_tier_calibration')
          .select('tier, window_label, hits, total, hit_rate, computed_date')
          .eq('sport', sportDb(sport))
          .eq('window_label', window)
          .order('computed_date', { ascending: false })
          .limit(200);
        data = fallback.data || [];
      }

      // Dedupe by tier (keep first = most recent due to order)
      const seen = new Set<string>();
      const dedup: CohortRow[] = [];
      for (const r of data || []) {
        if (seen.has(r.tier)) continue;
        seen.add(r.tier);
        dedup.push(r);
      }
      setRows(dedup);
    } catch (e: any) {
      console.warn('[CohortDashboard] fetch failed:', e?.message);
      setRows([]);
    } finally {
      setLoading(false);
    }
  };

  // Helper: resolve a tier to its display config — backend first, fallback second.
  const resolveMeta = (tier: string): { label: string; group: string; desc?: string; display_order?: number } | null => {
    const backend = labels[tier];
    if (backend) {
      return {
        label: backend.label,
        group: backend.group_name,
        desc: backend.description || undefined,
        display_order: backend.display_order,
      };
    }
    const fallback = FALLBACK_LABELS[tier];
    if (fallback) return fallback;
    return null;
  };

  // Group rows by category, ordered by backend display_order within each group
  const grouped: Record<string, (CohortRow & { meta: { label: string; group: string; desc?: string; display_order?: number } })[]> = {};
  for (const row of rows) {
    const meta = resolveMeta(row.tier);
    if (!meta) continue;  // Hide internal-only cohorts (no label config)
    if (!grouped[meta.group]) grouped[meta.group] = [];
    grouped[meta.group].push({ ...row, meta });
  }
  // Sort each group by display_order (backend) then label
  for (const group of Object.keys(grouped)) {
    grouped[group].sort((a, b) => {
      const aOrder = a.meta.display_order ?? 99;
      const bOrder = b.meta.display_order ?? 99;
      if (aOrder !== bOrder) return aOrder - bOrder;
      return a.meta.label.localeCompare(b.meta.label);
    });
  }
  // Use backend group order if available, else fallback alphabetical
  const renderGroups = groupOrder.length > 0
    ? groupOrder.filter(g => grouped[g])
    : Object.keys(grouped).sort();

  // Sport selector chips — always rendered so users can switch even when
  // current sport is offseason. Live sports are tappable; offseason ones
  // are disabled w/ "Returns Mar" style hint.
  const SportChips = (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipScroll} contentContainerStyle={styles.chipScrollContent}>
      {SPORTS_AVAILABLE.map(opt => {
        const isActive = sport === opt.sport;
        const isLive = isSportLive(opt.sport);
        return (
          <TouchableOpacity
            key={opt.sport}
            onPress={() => isLive && setSport(opt.sport)}
            disabled={!isLive}
            style={[
              styles.chip,
              isActive && styles.chipActive,
              !isLive && styles.chipDisabled,
            ]}
          >
            <Text style={[styles.chipText, isActive && styles.chipTextActive, !isLive && styles.chipTextDisabled]}>
              {opt.label}{!isLive ? ` · ${opt.disabledLabel}` : ''}
            </Text>
          </TouchableOpacity>
        );
      })}
    </ScrollView>
  );

  if (!isSportLive(sport)) {
    return (
      <View style={styles.container}>
        <View style={styles.headerRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.title}>How We Pick</Text>
            <Text style={styles.subtitle}>Live audited hit rates per signal cohort.</Text>
          </View>
        </View>
        {SportChips}
        <Text style={styles.offseasonText}>
          {sport} is offseason — cohort tracking activates when the season starts.
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.headerRow}>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>How We Pick</Text>
          <Text style={styles.subtitle}>
            Live audited hit rates per signal cohort. {hideSamples ? 'Upgrade to Pro to see sample sizes.' : 'Sample sizes shown.'}
          </Text>
        </View>
      </View>

      {/* Sport selector */}
      {SportChips}

      {/* Window selector */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipScroll} contentContainerStyle={styles.chipScrollContent}>
        {(['7d', '30d', 'std'] as const).map(w => (
          <TouchableOpacity key={w} onPress={() => setWindow(w)} style={[styles.chip, window === w && styles.chipActive]}>
            <Text style={[styles.chipText, window === w && styles.chipTextActive]}>
              {w === 'std' ? 'Lifetime' : w === '7d' ? 'Last 7D' : 'Last 30D'}
            </Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {loading ? (
        <ActivityIndicator size="small" color={BRAND_GREEN} style={{ marginTop: 30 }} />
      ) : rows.length === 0 ? (
        <Text style={styles.emptyText}>No cohort data yet for this window.</Text>
      ) : (
        <View>
          {renderGroups.map(group => (
            <View key={group} style={styles.groupBox}>
              <Text style={styles.groupHeader}>{group}</Text>
              {grouped[group].map((row, i) => (
                <CohortRowDisplay key={row.tier} row={row} hideSamples={hideSamples} isLast={i === grouped[group].length - 1} />
              ))}
            </View>
          ))}
        </View>
      )}

      <Text style={styles.footer}>
        Rates audited daily. Cohorts with fewer than 5 samples are hidden until they mature.
      </Text>
    </View>
  );
};

const CohortRowDisplay: React.FC<{ row: CohortRow & { meta: { label: string; desc?: string } }; hideSamples: boolean; isLast: boolean }> = ({ row, hideSamples, isLast }) => {
  if (row.total < 5) return null;  // Hide low-sample noise
  const pct = Math.round((row.hit_rate || 0) * 100);
  const color = pct >= 60 ? BRAND_GREEN : pct >= 50 ? BRAND_AMBER : RED;
  return (
    <View style={[styles.cohortRow, !isLast && styles.cohortRowBorder]}>
      <View style={{ flex: 1 }}>
        <Text style={styles.cohortName}>{row.meta.label}</Text>
        {row.meta.desc && <Text style={styles.cohortDesc}>{row.meta.desc}</Text>}
      </View>
      <View style={styles.cohortStats}>
        {!hideSamples && (
          <Text style={styles.cohortN}>{row.hits}-{row.total - row.hits}</Text>
        )}
        <Text style={[styles.cohortPct, { color }]}>{pct}%</Text>
        {!hideSamples && (
          <Text style={styles.cohortSample}>n={row.total}</Text>
        )}
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { backgroundColor: CARD_BG, borderRadius: 14, padding: 16, marginBottom: 14, borderWidth: 1, borderColor: BORDER },
  headerRow: { flexDirection: 'row', alignItems: 'flex-start', marginBottom: 8 },
  title: { color: TEXT_PRIMARY, fontWeight: '800', fontSize: 15 },
  subtitle: { color: TEXT_MUTED, fontSize: 11, marginTop: 4, lineHeight: 15 },
  chipScroll: { marginTop: 8, marginBottom: 14 },
  chipScrollContent: { gap: 6 },
  chip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 14, backgroundColor: 'rgba(122,146,168,0.10)', borderWidth: 1, borderColor: 'transparent' },
  chipActive: { backgroundColor: THEME.accent + '1A', borderColor: BRAND_GREEN },
  chipDisabled: { opacity: 0.4 },
  chipText: { color: TEXT_MUTED, fontSize: 11, fontWeight: '600' },
  chipTextActive: { color: BRAND_GREEN, fontWeight: '700' },
  chipTextDisabled: { fontSize: 10 },
  groupBox: { marginBottom: 14 },
  groupHeader: { color: TEXT_MUTED, fontSize: 10, fontWeight: '800', letterSpacing: 1.2, marginBottom: 6 },
  cohortRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 8 },
  cohortRowBorder: { borderBottomWidth: 0.5, borderBottomColor: BORDER },
  cohortName: { color: TEXT_PRIMARY, fontWeight: '600', fontSize: 13 },
  cohortDesc: { color: TEXT_MUTED, fontSize: 10, marginTop: 2 },
  cohortStats: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  cohortN: { color: TEXT_MUTED, fontSize: 11, minWidth: 40, textAlign: 'right' },
  cohortPct: { fontSize: 13, fontWeight: '800', minWidth: 40, textAlign: 'right' },
  cohortSample: { color: THEME.textMuted, fontSize: 9, minWidth: 36 },
  emptyText: { color: TEXT_MUTED, fontSize: 12, textAlign: 'center', marginTop: 20 },
  offseasonText: { color: TEXT_MUTED, fontSize: 12, marginTop: 8 },
  footer: { color: THEME.textMuted, fontSize: 10, lineHeight: 14, marginTop: 12, fontStyle: 'italic' },
});
