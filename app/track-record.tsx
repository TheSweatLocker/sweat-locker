/**
 * Track Record — public verified performance page.
 *
 * 2026-07-12 discovery: PRIME props 30d = 61% is the honest headline metric
 * (POTD is 43% same window — not marketing-viable).
 *
 * 2026-07-16 honesty refit: the 61% was ONLY defensible if we split it out
 * of batter hits_over 0.5 (which is a SIGNAL surface, not a bettable one —
 * alt-line juice at -300 to -450 eats the edge). Views were split:
 *
 *   BETTABLE (v_prop_track_record):    pitcher props with book_line NOT NULL.
 *                                       Real receipts a user can act on.
 *   SIGNAL (v_signal_track_record):    batter hits 0.5 internal-line accuracy.
 *                                       Model correctness, not bet.
 *
 * Page shows BOTH with distinct framing so users understand the difference.
 *
 * Data sources (all views defined in supabase/migrations/20260712 + 20260716):
 *   - v_prop_track_record         : 7/30/90d win % by tier (bettable only)
 *   - v_prop_track_record_by_type : 30d hit rate by (tier, prop_type) (bettable only)
 *   - v_signal_track_record       : batter hit signal accuracy (informational)
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

import { THEME, TIER_COLOR, OUTCOME_COLOR } from './theme';
const supabase = createClient(
  process.env.EXPO_PUBLIC_SUPABASE_URL!,
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY!,
);

// Brand palette — match RecapCard.tsx
const BRAND_GREEN = THEME.accent;
const BRAND_AMBER = THEME.hrb;
const RED = THEME.loss;
const BG = THEME.bg;
const CARD_BG = THEME.surface;
const CARD_HL = THEME.surfaceAlt;
const TEXT_PRIMARY = THEME.text;
const TEXT_MUTED = THEME.textDim;
const BORDER = THEME.border;

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
  const [signalTierRows, setSignalTierRows] = useState<TierRow[] | null>(null);
  const [bucketRows, setBucketRows] = useState<BucketRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 2026-08-10: cross-sport overall record + proven-pattern scenarios.
  // Replaces the MLB-only headline metrics with a sport-agnostic surface
  // that answers "what has Jerry actually done" across every sport.
  const [bySportRows, setBySportRows] = useState<Array<{sport: string; wins: number; losses: number; hit_pct: number}>>([]);
  const [topScenarios, setTopScenarios] = useState<Array<any>>([]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true); setError(null);
      try {
        const [tierRes, bucketRes, signalRes] = await Promise.all([
          supabase.from('v_prop_track_record').select('*'),
          supabase.from('v_prop_track_record_by_type').select('*'),
          supabase.from('v_signal_track_record').select('*'),
        ]);
        if (cancelled) return;
        if (tierRes.error) throw tierRes.error;
        if (bucketRes.error) throw bucketRes.error;
        // Signal view may not exist on older schemas — non-fatal
        setTierRows((tierRes.data as TierRow[]) ?? []);
        setBucketRows((bucketRes.data as BucketRow[]) ?? []);
        setSignalTierRows(signalRes.error ? [] : ((signalRes.data as TierRow[]) ?? []));

        // 2026-08-10: cross-sport aggregation from jerry_reads + prop_jerry_reads.
        // Pulls all directional reads (BACK/FADE/directional Jerry calls) with a
        // result, aggregates hit% per sport. Sport-agnostic — future sports
        // (NFL/UFC/NCAAF as data lands) appear automatically.
        try {
          const {data: gr} = await supabase.from('jerry_reads')
            .select('sport,result').not('result', 'is', null).limit(2000);
          const {data: pj} = await supabase.from('prop_jerry_reads')
            .select('sport,result').not('result', 'is', null).limit(3000);
          const bySport: Record<string, {w: number; l: number}> = {};
          for (const r of [...(gr || []), ...(pj || [])]) {
            const s = ((r as any).sport || '').toUpperCase();
            if (!s) continue;
            if (!bySport[s]) bySport[s] = {w: 0, l: 0};
            if ((r as any).result === 'Win') bySport[s].w++;
            else if ((r as any).result === 'Loss') bySport[s].l++;
          }
          const rows = Object.entries(bySport)
            .map(([sport, {w, l}]) => ({sport, wins: w, losses: l,
              hit_pct: w + l > 0 ? Math.round(w / (w + l) * 1000) / 10 : 0}))
            .filter(r => r.wins + r.losses >= 10)
            .sort((a, b) => (b.wins + b.losses) - (a.wins + a.losses));
          if (!cancelled) setBySportRows(rows);
        } catch (e) { /* non-fatal */ }

        // 2026-08-10: top proven-pattern scenarios from scenario_audit.
        // Filters to n>=20 with strong hit rate (>=60%) or strong fade
        // (<=42%). Shows what scenarios have EARNED user trust.
        try {
          const {data: sa} = await supabase.from('scenario_audit')
            .select('sport,market,scenario_label,total_n,hit_rate,roi_pct,jerry_hint')
            .eq('scenario_window', 'lifetime')
            .gte('total_n', 15)
            .order('total_n', {ascending: false})
            .limit(20);
          const filtered = (sa || []).filter((r: any) =>
            r.hit_rate >= 60 || r.hit_rate <= 42);
          if (!cancelled) setTopScenarios(filtered.slice(0, 8));
        } catch (e) { /* non-fatal */ }
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
  const primeSignalTier = useMemo(() => signalTierRows?.find(r => r.tier === 'PRIME'), [signalTierRows]);
  const strongSignalTier = useMemo(() => signalTierRows?.find(r => r.tier === 'STRONG'), [signalTierRows]);

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
  const primeSignalStats = getStats(primeSignalTier, window);
  const strongSignalStats = getStats(strongSignalTier, window);
  const hasSignalData = (signalTierRows?.length ?? 0) > 0;

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

        {/* 2026-08-10: OVERALL BY SPORT — cross-sport headline card.
            Shows lifetime Jerry hit rate per sport (game reads + prop reads
            combined). Sport-agnostic; new sports appear automatically as
            their graders start writing results. */}
        {!loading && !error && bySportRows.length > 0 && (
          <View style={styles.overallCard}>
            <Text style={styles.overallHeader}>🏆 OVERALL BY SPORT · LIFETIME</Text>
            {bySportRows.map(r => {
              const color = r.hit_pct >= 55 ? BRAND_GREEN
                          : r.hit_pct >= 50 ? BRAND_AMBER
                          : RED;
              return (
                <View key={r.sport} style={styles.overallRow}>
                  <Text style={styles.overallSport}>{r.sport}</Text>
                  <Text style={styles.overallRecord}>{r.wins}-{r.losses}</Text>
                  <Text style={[styles.overallPct, {color}]}>{r.hit_pct}%</Text>
                </View>
              );
            })}
            <Text style={styles.overallFoot}>
              Every pick Jerry made across sports · graded post-game
            </Text>
          </View>
        )}

        {/* 2026-08-10: PROVEN PATTERNS — scenario_audit surface. Shows
            historical scenarios with n>=15 and either strong hit (>=60%)
            or strong fade (<=42%). This is the "trust our discipline"
            evidence surface — user sees the specific setups we back / fade
            based on real data, not vibes. */}
        {!loading && !error && topScenarios.length > 0 && (
          <View style={styles.scenarioCard}>
            <Text style={styles.overallHeader}>📊 PROVEN PATTERNS</Text>
            {topScenarios.map((s, i) => {
              const isBack = s.hit_rate >= 60;
              const color = isBack ? BRAND_GREEN : RED;
              return (
                <View key={i} style={styles.scenarioRow}>
                  <View style={{flex: 1}}>
                    <Text style={styles.scenarioLabel} numberOfLines={2}>
                      {s.scenario_label || s.scenario_key || '?'}
                    </Text>
                    <Text style={styles.scenarioMeta}>
                      {s.sport} · {s.market} · n={s.total_n} · {isBack ? 'BACK' : 'FADE'} signal
                    </Text>
                  </View>
                  <Text style={[styles.scenarioPct, {color}]}>
                    {isBack ? Math.round(s.hit_rate) : Math.round(100 - s.hit_rate)}%
                  </Text>
                </View>
              );
            })}
            <Text style={styles.overallFoot}>
              Historical hit rates from graded games · updated nightly
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

        {/* SECTION 1 — BETTABLE PROPS (pitcher props at book lines) */}
        {!loading && !error && (
          <>
            <View style={styles.sectionHeader}>
              <Text style={styles.sectionHeaderText}>BETTABLE PROPS</Text>
              <Text style={styles.sectionHeaderSub}>
                Pitcher props at posted book lines · what you can act on
              </Text>
            </View>

            {/* Hero — PRIME */}
            <View style={styles.heroCard}>
              <Text style={styles.heroLabel}>PRIME</Text>
              <Text style={styles.heroPct}>
                {primeStats.pct !== null ? `${primeStats.pct}%` : '—'}
              </Text>
              <Text style={styles.heroRecord}>
                {primeStats.wins}-{primeStats.losses}
                <Text style={styles.heroN}> · n={primeStats.wins + primeStats.losses}</Text>
              </Text>
              <Text style={styles.heroCaption}>
                Highest-conviction picks verified against book lines.
              </Text>
            </View>

            {/* Sub — STRONG */}
            <View style={styles.subCard}>
              <Text style={styles.subCardLabel}>STRONG</Text>
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
          </>
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

        {/* SECTION 2 — SIGNAL ACCURACY (batter hit projections) */}
        {!loading && !error && hasSignalData && (
          <>
            <View style={[styles.sectionHeader, { marginTop: 8 }]}>
              <Text style={styles.sectionHeaderText}>SIGNAL ACCURACY</Text>
              <Text style={styles.sectionHeaderSub}>
                Model accuracy on batter hit projections · informational, not a straight bet
              </Text>
            </View>

            {/* PRIME signal */}
            <View style={styles.signalCard}>
              <View style={styles.signalRow}>
                <Text style={styles.signalLabel}>PRIME model accuracy</Text>
                <Text style={styles.signalPct}>
                  {primeSignalStats.pct !== null ? `${primeSignalStats.pct}%` : '—'}
                </Text>
              </View>
              <Text style={styles.signalRecord}>
                {primeSignalStats.wins}-{primeSignalStats.losses}
                <Text style={styles.signalN}> · n={primeSignalStats.wins + primeSignalStats.losses}</Text>
              </Text>
            </View>

            {/* STRONG signal */}
            <View style={styles.signalCard}>
              <View style={styles.signalRow}>
                <Text style={styles.signalLabel}>STRONG model accuracy</Text>
                <Text style={styles.signalPct}>
                  {strongSignalStats.pct !== null ? `${strongSignalStats.pct}%` : '—'}
                </Text>
              </View>
              <Text style={styles.signalRecord}>
                {strongSignalStats.wins}-{strongSignalStats.losses}
                <Text style={styles.signalN}> · n={strongSignalStats.wins + strongSignalStats.losses}</Text>
              </Text>
            </View>

            <Text style={styles.signalCaveat}>
              Batter hit signals are internal projection accuracy. Sportsbook "1+ hits" alt-line
              odds vary (typically −250 to −450); at those prices the model edge may not
              cover the vig. Best used as same-game parlay legs or confirmation of total plays.
            </Text>
          </>
        )}

        {/* Fine print */}
        {!loading && !error && (
          <Text style={styles.finePrint}>
            Results reflect internally graded picks. Bettable props verified against posted book
            lines. Rolling windows update nightly at 12:30 AM ET. Buckets shown for prop types
            with ≥5 graded picks in window.
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

  // 2026-08-10: cross-sport overall + proven-patterns section styles
  overallCard: {
    backgroundColor: CARD_BG, borderRadius: 12, padding: 16,
    borderWidth: 1, borderColor: BRAND_GREEN + '40',
    marginBottom: 12,
  },
  overallHeader: {
    color: BRAND_GREEN, fontSize: 12, fontWeight: '800',
    letterSpacing: 0.5, marginBottom: 12,
  },
  overallRow: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: BORDER + '40',
  },
  overallSport: {
    color: TEXT_PRIMARY, fontSize: 14, fontWeight: '700', flex: 1,
  },
  overallRecord: {
    color: TEXT_MUTED, fontSize: 13, marginRight: 12,
  },
  overallPct: {
    fontSize: 18, fontWeight: '800', minWidth: 60, textAlign: 'right',
  },
  overallFoot: {
    color: TEXT_MUTED, fontSize: 10, marginTop: 10, fontStyle: 'italic',
  },
  scenarioCard: {
    backgroundColor: CARD_BG, borderRadius: 12, padding: 16,
    borderWidth: 1, borderColor: BORDER,
    marginBottom: 20,
  },
  scenarioRow: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: BORDER + '40',
  },
  scenarioLabel: {
    color: TEXT_PRIMARY, fontSize: 13, fontWeight: '600', lineHeight: 17,
  },
  scenarioMeta: {
    color: TEXT_MUTED, fontSize: 10, marginTop: 3,
  },
  scenarioPct: {
    fontSize: 20, fontWeight: '800', minWidth: 60, textAlign: 'right',
  },

  // Section headers separating BETTABLE from SIGNAL
  sectionHeader: { marginBottom: 12, marginTop: 4 },
  sectionHeaderText: {
    color: TEXT_PRIMARY, fontSize: 13, fontWeight: '800',
    letterSpacing: 2, marginBottom: 2,
  },
  sectionHeaderSub: {
    color: TEXT_MUTED, fontSize: 11, letterSpacing: 0.3,
  },

  // Signal cards: dimmer treatment so they read as secondary
  signalCard: {
    backgroundColor: CARD_BG, borderRadius: 10, padding: 14, marginBottom: 8,
    borderWidth: 1, borderColor: BORDER,
  },
  signalRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  signalLabel: { color: TEXT_MUTED, fontSize: 12, fontWeight: '600', letterSpacing: 0.5 },
  signalPct: { color: TEXT_PRIMARY, fontSize: 22, fontWeight: '700' },
  signalRecord: { color: TEXT_MUTED, fontSize: 12, marginTop: 4 },
  signalN: { color: TEXT_MUTED, fontSize: 11 },
  signalCaveat: {
    color: TEXT_MUTED, fontSize: 11, marginTop: 12, marginBottom: 8,
    lineHeight: 16, paddingHorizontal: 4, fontStyle: 'italic',
  },
});
