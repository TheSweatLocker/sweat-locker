/**
 * RecapStrip — compact 1-line receipts banner on Home tab.
 *
 * Conditional surfacing logic (decided 2026-05-22):
 *   Default:  shows 30D rolling card hit rate (stable, smooths variance)
 *   Bonus:    when yesterday was a win day (>=60% on n>=5), surfaces
 *             "Yesterday X-Y ✓" alongside the 30D number
 *
 * Why conditional: a 4-9 day on a new app's home tab kills trust before
 * users build it. The 30D rolling number is honest (it INCLUDES bad days,
 * we just don't lead with single-day variance). On hot days we still get
 * the marketing punch. Cold days don't tank first impressions.
 *
 * Tap → opens Jerry → Receipts sub-tab for full breakdown (per-day, per-
 * tier, cohort dashboard).
 *
 * Reads receipts unit from sweat_card top_8_summary (which the resolver
 * fills nightly). For 30D aggregation, queries last 30 days of
 * jerry_cache sweat_card entries and sums.
 *
 * Sport-universal — uses sportPeriods helpers for cadence-aware labels.
 */
import React, { useEffect, useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { createClient } from '@supabase/supabase-js';
import { Sport, isSportLive } from '../lib/sportPeriods';

import { THEME, TIER_COLOR, OUTCOME_COLOR } from '../theme';
const supabase = createClient(
  process.env.EXPO_PUBLIC_SUPABASE_URL!,
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY!,
);

const BRAND_GREEN = THEME.accent;
const BRAND_AMBER = THEME.hrb;
const CARD_BG = THEME.surface;
const TEXT_PRIMARY = THEME.text;
const TEXT_MUTED = THEME.textDim;
const RED = THEME.loss;

// 2026-09-03 SERVER-OVERRIDABLE THRESHOLDS. Prior constants were
// hardcoded — changing them required an App Store ship. Now: on mount
// we try to load overrides from jerry_cache.cache_key='config_recap_strip'.
// If DB row missing/unenabled → use defaults below. Change thresholds
// via SQL:
//   INSERT INTO jerry_cache (cache_key, sport, data, ...)
//   VALUES ('config_recap_strip', 'ALL', jsonb_build_object(
//     'threshold_pct', 55, 'min_sample', 3,
//     'min_resolved', 20, 'min_days', 3
//   ), ...)
const DEFAULT_SURFACE_THRESHOLD_PCT = 60;   // yesterday shown if hit >= this
const DEFAULT_SURFACE_MIN_SAMPLE = 5;
const DEFAULT_MIN_RESOLVED = 30;
const DEFAULT_MIN_DAYS = 5;

type Props = {
  sport: Sport;
  onTap?: () => void;
};

type StripData = {
  yesterdayWins: number;
  yesterdayLosses: number;
  yesterdayPushes: number;
  yesterdayPending: number;
  last30Wins: number;
  last30Losses: number;
  last30Days: number;
  effectiveSport: Sport;  // What sport was actually loaded (may differ from requested)
};

// Fallback chain — when the requested sport has no resolved data yet,
// try the next sport. MLB has the most data and ships first, so it's
// the final fallback. Order roughly matches "most data → least data".
const SPORT_FALLBACK_ORDER: Sport[] = ['MLB', 'NBA', 'NCAAB', 'NFL'];

export const RecapStrip: React.FC<Props> = ({ sport, onTap }) => {
  const [data, setData] = useState<StripData | null>(null);
  const [loading, setLoading] = useState(true);
  const [cfg, setCfg] = useState<{
    thresholdPct: number, minSample: number, minResolved: number, minDays: number
  }>({thresholdPct: DEFAULT_SURFACE_THRESHOLD_PCT, minSample: DEFAULT_SURFACE_MIN_SAMPLE,
      minResolved: DEFAULT_MIN_RESOLVED, minDays: DEFAULT_MIN_DAYS});

  useEffect(() => {
    // Load server config overrides (once). Silently falls back to defaults
    // if row missing / DB read fails — no crash, no flicker.
    (async () => {
      try {
        const {data: rows} = await supabase.from('jerry_cache')
          .select('data')
          .eq('cache_key', 'config_recap_strip')
          .limit(1);
        const d: any = rows && rows[0]?.data;
        if (d) {
          setCfg({
            thresholdPct: Number(d.threshold_pct ?? DEFAULT_SURFACE_THRESHOLD_PCT),
            minSample:    Number(d.min_sample    ?? DEFAULT_SURFACE_MIN_SAMPLE),
            minResolved:  Number(d.min_resolved  ?? DEFAULT_MIN_RESOLVED),
            minDays:      Number(d.min_days      ?? DEFAULT_MIN_DAYS),
          });
        }
      } catch {}
    })();
    fetchStripData();
  }, [sport]);

  // Helper: fetch one sport's 30d recap data. Returns the strip shape
  // (without effectiveSport) or null if no resolved data exists.
  const fetchOneSport = async (s: Sport): Promise<Omit<StripData, 'effectiveSport'> | null> => {
    if (!isSportLive(s)) return null;
    const { data: rows, error } = await supabase
      .from('jerry_cache')
      .select('cache_key, data')
      .ilike('cache_key', 'sweat_card_%')
      .eq('sport', s.toUpperCase())
      .order('cache_key', { ascending: false })
      .limit(30);
    if (error) {
      console.warn(`[RecapStrip] ${s} fetch error:`, error.message);
      return null;
    }
    let yWins = 0, yLosses = 0, yPushes = 0, yPending = 0;
    let l30Wins = 0, l30Losses = 0;
    let daysWithResults = 0;
    const yesterdayKey = `sweat_card_${getYesterdayET()}`;
    for (const row of (rows || [])) {
      const d: any = typeof row.data === 'string' ? JSON.parse(row.data) : row.data;
      const summary = d?.top_8_summary;
      if (!summary) continue;
      if (row.cache_key === yesterdayKey) {
        yWins = summary.wins || 0;
        yLosses = summary.losses || 0;
        yPushes = summary.pushes || 0;
        yPending = summary.pending || 0;
      }
      if (summary.resolved > 0) {
        l30Wins += summary.wins || 0;
        l30Losses += summary.losses || 0;
        daysWithResults++;
      }
    }
    if (l30Wins + l30Losses === 0) return null;  // no resolved data
    return {
      yesterdayWins: yWins,
      yesterdayLosses: yLosses,
      yesterdayPushes: yPushes,
      yesterdayPending: yPending,
      last30Wins: l30Wins,
      last30Losses: l30Losses,
      last30Days: daysWithResults,
    };
  };

  const fetchStripData = async () => {
    setLoading(true);
    try {
      // Try the requested sport first; fall back to other live sports in
      // priority order. Avoids the bug where games-tab=NBA but no NBA
      // sweat-card data exists yet, so the strip went silent. Now it'll
      // show whatever sport HAS receipts (typically MLB during summer).
      const tryOrder = [sport, ...SPORT_FALLBACK_ORDER.filter(s => s !== sport)];
      for (const s of tryOrder) {
        const result = await fetchOneSport(s);
        if (result) {
          setData({ ...result, effectiveSport: s });
          return;
        }
      }
      setData(null);
    } catch (e: any) {
      console.warn('[RecapStrip] exception:', e?.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.strip}>
        <ActivityIndicator size="small" color={BRAND_GREEN} />
      </View>
    );
  }

  // Minimum sample gates added 2026-05-24 after user (correctly) flagged
  // that "MLB Last 30D 5-11" reading was misleading. Backstory: top_8
  // curation only shipped 5/22, so by 5/24 we had 2 days × 8 picks = 16
  // graded — both of which happened to be bad days (3-5 + 2-6). Labeling
  // 16 picks over 2 days as "30D" is mathematically true but
  // presentationally a lie. Below these thresholds we'd rather show
  // NOTHING than mislead with a small-sample number.
  if (!data) return null;
  const l30TotalCheck = data.last30Wins + data.last30Losses;
  if (l30TotalCheck === 0) return null;          // no resolved data
  if (l30TotalCheck < cfg.minResolved) return null; // sample too small to claim a rate
  if (data.last30Days < cfg.minDays) return null;   // too few days to call it "30D"

  const l30Total = data.last30Wins + data.last30Losses;
  const l30Pct = l30Total > 0 ? Math.round((data.last30Wins / l30Total) * 100) : 0;
  const l30Color = l30Pct >= 60 ? BRAND_GREEN : l30Pct >= 50 ? BRAND_AMBER : RED;

  // Conditional yesterday surfacing
  const yResolved = data.yesterdayWins + data.yesterdayLosses;
  const yPct = yResolved > 0 ? (data.yesterdayWins / yResolved) * 100 : 0;
  const showYesterday = yResolved >= cfg.minSample && yPct >= cfg.thresholdPct;

  return (
    <TouchableOpacity activeOpacity={0.85} onPress={onTap} style={styles.strip}>
      <View style={styles.content}>
        {showYesterday && (
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>YESTERDAY</Text>
            <View style={styles.statRow}>
              <Text style={styles.statValue}>
                {data.yesterdayWins}-{data.yesterdayLosses}
              </Text>
              <Text style={[styles.checkmark, { color: BRAND_GREEN }]}>✓</Text>
            </View>
          </View>
        )}

        {showYesterday && <View style={styles.divider} />}

        <View style={styles.section}>
          <Text style={styles.sectionLabel}>
            {data.effectiveSport !== sport ? `${data.effectiveSport} ` : ''}LAST 30D
          </Text>
          <View style={styles.statRow}>
            <Text style={styles.statValue}>
              {data.last30Wins}-{data.last30Losses}
            </Text>
            <Text style={[styles.statPct, { color: l30Color }]}>{l30Pct}%</Text>
          </View>
        </View>

        <Text style={styles.tapHint}>›</Text>
      </View>
    </TouchableOpacity>
  );
};

function getYesterdayET(): string {
  const d = new Date(Date.now() - 4 * 60 * 60 * 1000 - 24 * 60 * 60 * 1000);
  return d.toISOString().slice(0, 10);
}

const styles = StyleSheet.create({
  strip: {
    backgroundColor: CARD_BG,
    borderRadius: 12,
    paddingVertical: 10,
    paddingHorizontal: 14,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: THEME.border,
  },
  content: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
  },
  section: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  sectionLabel: {
    color: TEXT_MUTED,
    fontSize: 9,
    fontWeight: '800',
    letterSpacing: 1.2,
  },
  statRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 6,
  },
  statValue: {
    color: TEXT_PRIMARY,
    fontSize: 14,
    fontWeight: '800',
  },
  statPct: {
    fontSize: 13,
    fontWeight: '800',
  },
  checkmark: {
    fontSize: 14,
    fontWeight: '900',
  },
  divider: {
    width: 1,
    height: 18,
    backgroundColor: THEME.border,
  },
  tapHint: {
    color: TEXT_MUTED,
    fontSize: 16,
    marginLeft: 'auto',
  },
});
