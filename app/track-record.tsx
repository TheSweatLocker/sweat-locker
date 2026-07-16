/**
 * Track Record — public verified performance page.
 *
 * 2026-07-12 discovery: PRIME props 30d = 61% is the honest headline metric
 * (POTD is 43% same window — not marketing-viable). This page fronts the
 * props record with a clear rolling-window toggle and a bucket-level truth
 * table so users can see WHERE the edge lives, not just tier-averaged wins.
 *
 * Data sources (all views defined in supabase/migrations/20260712_track_record_views.sql):
 *   - v_prop_track_record       : 7/30/90d win % by tier
 *   - v_prop_track_record_by_type : 30d hit rate by (tier, prop_type)
 *   - v_potd_track_record       : POTD per sport (secondary section, framed honestly)
 *
 * Navigation: reachable from main app tab or deep link. Route is
 * /track-record via Expo Router.
 */
import React, { useEffect, useState, useMemo } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity, ActivityIndicator,
  StyleSheet, StatusBar,
} from 'react-native';
import { Stack, useRouter } from 'expo-router';
import { createClient } from '@supabase/supabase-js';

const supabase = createClient(
  process.env.EXPO_PUBLIC_SUPABASE_URL!,
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY!,
);

// Brand palette — match RecapCard.tsx
const BRAND_GREEN = '#00e5a0';
const BRAND_AMBER = '#ffb800';
const RED = '#ff4d6d';
const BG = '#080c10';
const CARD_BG = '#0d1419';
const CARD_HL = '#152029';
const TEXT_PRIMARY = '#e8f0f8';
const TEXT_MUTED = '#7a92a8';
const BORDER = '#1f2d3d';

type Window = '7d' | '30d' | '90d';

type TierRow = {
  tier: 'PRIME' | 'STRONG';
  wins_7d: number; losses_7d: number; pct_7d: number | null;
  wins_30d: number; losses_30d: number; pct_30d: number | null;
  wins_90d: number; losses_90d: number; pct_90d: number | null;
};

type BucketRow = {
  tier: 'PRIME' | 'STRONG';
  prop_type: string;
  wins: number;
  losses: number;
  n: number;
  pct: number | null;
};

const PROP_TYPE_LABEL: Record<string, string> = {
  bb_under: 'BB Under',
  bb_over: 'BB Over',
  er_under: 'ER Under',
  er_over: 'ER Over',
  ha_under: 'Hits-Allowed Under',
  ha_over: 'Hits-Allowed Over',
  ks_under: 'K Under',
  ks_over: 'K Over',
  outs_under: 'Outs Under',
  outs_over: 'Outs Over',
  hits_over: 'Hits Over',
  hits_under: 'Hits Under',
};

const labelFor = (t: string) => PROP_TYPE_LABEL[t] ?? t;

export default function TrackRecord() {
  const router = useRouter();
  const [window, setWindow] = useState<Window>('30d');
  const [tierRows, setTierRows] = useState<TierRow[] | null>(null);
  const [bucketRows, setBucketRows] = useState<BucketRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true); setError(null);
      try {
        const [tierRes, bucketRes] = await Promise.all([
          supabase.from('v_prop_track_record').select('*'),
          supabase.from('v_prop_track_record_by_type').select('*'),
        ]);
        if (cancelled) return;
        if (tierRes.error) throw tierRes.error;
        if (bucketRes.error) throw bucketRes.error;
        setTierRows((tierRes.data as TierRow[]) ?? []);
        setBucketRows((bucketRes.data as BucketRow[]) ?? []);
      } catch (e: any) {
        if (!cancelled) setError(e?.message ?? 'failed to load');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, []);

  const primeTier = useMemo(() => tierRows?.find(r => r.tier === 'PRIME'), [tierRows]);
  const strongTier = useMemo(() => tierRows?.find(r => r.tier === 'STRONG'), [tierRows]);

  const getStats = (row: TierRow | undefined, w: Window) => {
    if (!row) return { wins: 0, losses: 0, pct: null as number | null };
    return {
      wins: row[`wins_${w}` as const] ?? 0,
      losses: row[`losses_${w}` as const] ?? 0,
      pct: row[`pct_${w}` as const],
    };
  };

  const primeStats = getStats(primeTier, window);
  const strongStats = getStats(strongTier, window);

  // Sort buckets by hit % desc, KEEP highlighted (>=60%), KILL flagged (<45%)
  const sortedBuckets = useMemo(() => {
    if (!bucketRows) return [];
    return [...bucketRows].sort((a, b) => (b.pct ?? 0) - (a.pct ?? 0));
  }, [bucketRows]);

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor={BG} />
      <Stack.Screen options={{ title: 'Track Record', headerShown: false }} />

      <ScrollView contentContainerStyle={styles.scroll}>
        {/* Header */}
        <View style={styles.headerRow}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
            <Text style={styles.backText}>←</Text>
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Track Record</Text>
          <View style={{ width: 40 }} />
        </View>

        <Text style={styles.subtitle}>
          Verified performance on card-eligible picks. Updated nightly.
        </Text>

        {/* Loading */}
        {loading && (
          <View style={styles.loadingBox}>
            <ActivityIndicator color={BRAND_GREEN} />
            <Text style={styles.loadingText}>Loading verified record…</Text>
          </View>
        )}

        {/* Error */}
        {error && (
          <View style={styles.errorBox}>
            <Text style={styles.errorText}>Couldn't load track record: {error}</Text>
            <Text style={styles.errorHint}>
              If this persists, the aggregation views may not be applied yet.
              Contact support.
            </Text>
          </View>
        )}

        {/* Window toggle */}
        {!loading && !error && (
          <View style={styles.windowRow}>
            {(['7d', '30d', '90d'] as Window[]).map(w => (
              <TouchableOpacity
                key={w}
                onPress={() => setWindow(w)}
                style={[styles.windowBtn, window === w && styles.windowBtnActive]}
              >
                <Text style={[styles.windowText, window === w && styles.windowTextActive]}>
                  {w === '7d' ? '7 Days' : w === '30d' ? '30 Days' : '90 Days'}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        )}

        {/* Hero — PRIME */}
        {!loading && !error && (
          <View style={styles.heroCard}>
            <Text style={styles.heroLabel}>PRIME PROPS</Text>
            <Text style={styles.heroPct}>
              {primeStats.pct !== null ? `${primeStats.pct}%` : '—'}
            </Text>
            <Text style={styles.heroRecord}>
              {primeStats.wins}-{primeStats.losses}
              <Text style={styles.heroN}> · n={primeStats.wins + primeStats.losses}</Text>
            </Text>
            <Text style={styles.heroCaption}>
              Highest-conviction pipeline picks with book-line verification.
            </Text>
          </View>
        )}

        {/* Sub — STRONG */}
        {!loading && !error && (
          <View style={styles.subCard}>
            <Text style={styles.subCardLabel}>STRONG PROPS</Text>
            <View style={styles.subCardRow}>
              <Text style={styles.subCardPct}>
                {strongStats.pct !== null ? `${strongStats.pct}%` : '—'}
              </Text>
              <Text style={styles.subCardRecord}>
                {strongStats.wins}-{strongStats.losses}
                <Text style={styles.subCardN}> · n={strongStats.wins + strongStats.losses}</Text>
              </Text>
            </View>
          </View>
        )}

        {/* Bucket-level truth table */}
        {!loading && !error && sortedBuckets.length > 0 && (
          <>
            <Text style={styles.sectionTitle}>Where the edge lives (30d)</Text>
            <Text style={styles.sectionSubtitle}>
              Hit rate by prop type. <Text style={{ color: BRAND_GREEN }}>Green</Text> = proven edge (≥60%),{' '}
              <Text style={{ color: RED }}>red</Text> = fade (&lt;45%).
            </Text>
            <View style={styles.tableCard}>
              <View style={styles.tableHeader}>
                <Text style={[styles.th, { flex: 1 }]}>Tier</Text>
                <Text style={[styles.th, { flex: 2 }]}>Type</Text>
                <Text style={[styles.th, { flex: 1, textAlign: 'right' }]}>W-L</Text>
                <Text style={[styles.th, { flex: 1, textAlign: 'right' }]}>%</Text>
              </View>
              {sortedBuckets.map((b, i) => {
                const pct = b.pct ?? 0;
                const isKeep = pct >= 60;
                const isKill = pct < 45;
                return (
                  <View key={`${b.tier}-${b.prop_type}-${i}`}
                    style={[styles.tableRow, i % 2 === 0 && styles.tableRowAlt]}
                  >
                    <Text style={[styles.td, { flex: 1, color: TEXT_MUTED }]}>{b.tier}</Text>
                    <Text style={[styles.td, { flex: 2 }]}>{labelFor(b.prop_type)}</Text>
                    <Text style={[styles.td, { flex: 1, textAlign: 'right', color: TEXT_MUTED }]}>
                      {b.wins}-{b.losses}
                    </Text>
                    <Text style={[
                      styles.td,
                      { flex: 1, textAlign: 'right', fontWeight: '700' },
                      isKeep && { color: BRAND_GREEN },
                      isKill && { color: RED },
                    ]}>
                      {b.pct !== null ? `${b.pct}%` : '—'}
                    </Text>
                  </View>
                );
              })}
            </View>
          </>
        )}

        {/* Fine print */}
        {!loading && !error && (
          <Text style={styles.finePrint}>
            Results reflect internally graded picks against posted book lines.
            Rolling windows update nightly at 12:30 AM ET.
            Buckets shown only for prop types with ≥5 graded picks in window.
          </Text>
        )}

        <View style={{ height: 40 }} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: BG },
  scroll: { padding: 16, paddingTop: 48 },

  headerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginBottom: 8,
  },
  backBtn: { padding: 8, width: 40 },
  backText: { color: TEXT_PRIMARY, fontSize: 24, fontWeight: '600' },
  headerTitle: { color: TEXT_PRIMARY, fontSize: 22, fontWeight: '700' },
  subtitle: { color: TEXT_MUTED, fontSize: 14, marginBottom: 20, textAlign: 'center' },

  loadingBox: { alignItems: 'center', padding: 40 },
  loadingText: { color: TEXT_MUTED, marginTop: 12, fontSize: 14 },

  errorBox: {
    backgroundColor: CARD_BG, borderRadius: 12, padding: 16,
    borderWidth: 1, borderColor: RED,
  },
  errorText: { color: RED, fontSize: 14, fontWeight: '600' },
  errorHint: { color: TEXT_MUTED, fontSize: 12, marginTop: 6 },

  windowRow: {
    flexDirection: 'row', backgroundColor: CARD_BG, borderRadius: 12,
    padding: 4, marginBottom: 16, borderWidth: 1, borderColor: BORDER,
  },
  windowBtn: { flex: 1, paddingVertical: 10, alignItems: 'center', borderRadius: 8 },
  windowBtnActive: { backgroundColor: CARD_HL },
  windowText: { color: TEXT_MUTED, fontSize: 13, fontWeight: '600' },
  windowTextActive: { color: BRAND_GREEN },

  heroCard: {
    backgroundColor: CARD_BG, borderRadius: 14, padding: 24, marginBottom: 12,
    borderWidth: 1, borderColor: BORDER, alignItems: 'center',
  },
  heroLabel: {
    color: BRAND_GREEN, fontSize: 12, fontWeight: '700',
    letterSpacing: 1.5, marginBottom: 8,
  },
  heroPct: { color: TEXT_PRIMARY, fontSize: 56, fontWeight: '800', lineHeight: 60 },
  heroRecord: { color: TEXT_PRIMARY, fontSize: 18, fontWeight: '600', marginTop: 4 },
  heroN: { color: TEXT_MUTED, fontSize: 14, fontWeight: '400' },
  heroCaption: { color: TEXT_MUTED, fontSize: 12, marginTop: 8, textAlign: 'center' },

  subCard: {
    backgroundColor: CARD_BG, borderRadius: 12, padding: 16,
    borderWidth: 1, borderColor: BORDER, marginBottom: 20,
  },
  subCardLabel: {
    color: BRAND_AMBER, fontSize: 11, fontWeight: '700',
    letterSpacing: 1.5, marginBottom: 6,
  },
  subCardRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between' },
  subCardPct: { color: TEXT_PRIMARY, fontSize: 28, fontWeight: '700' },
  subCardRecord: { color: TEXT_PRIMARY, fontSize: 14, fontWeight: '600' },
  subCardN: { color: TEXT_MUTED, fontSize: 12, fontWeight: '400' },

  sectionTitle: {
    color: TEXT_PRIMARY, fontSize: 16, fontWeight: '700', marginTop: 4, marginBottom: 4,
  },
  sectionSubtitle: {
    color: TEXT_MUTED, fontSize: 12, marginBottom: 12,
  },

  tableCard: {
    backgroundColor: CARD_BG, borderRadius: 12,
    borderWidth: 1, borderColor: BORDER, overflow: 'hidden', marginBottom: 20,
  },
  tableHeader: {
    flexDirection: 'row', backgroundColor: CARD_HL, paddingVertical: 10, paddingHorizontal: 12,
  },
  th: { color: TEXT_MUTED, fontSize: 11, fontWeight: '700', letterSpacing: 0.5 },
  tableRow: {
    flexDirection: 'row', paddingVertical: 10, paddingHorizontal: 12,
    borderTopWidth: 1, borderTopColor: BORDER,
  },
  tableRowAlt: { backgroundColor: '#0a1015' },
  td: { color: TEXT_PRIMARY, fontSize: 13 },

  finePrint: {
    color: TEXT_MUTED, fontSize: 11, textAlign: 'center', marginTop: 8,
    lineHeight: 16, paddingHorizontal: 16,
  },
});
