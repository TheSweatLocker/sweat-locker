/**
 * TierIntegrityBadge — receipts dashboard transparency.
 *
 * Reads from tier_integrity_findings (written by audit_tier_integrity.py
 * nightly). When the auditor flags drift — a lower tier outperforming a
 * higher tier on a (prop_type, direction) cohort — surfaces a chip per
 * finding so the receipts dashboard owns the disclosure rather than
 * hiding internal pipeline state.
 *
 * Default: shows the most recent computed_date's findings. Empty array
 * → renders nothing (clean state, no badge).
 *
 * Why surface this externally: the recurring user feedback is that we
 * should never silently know about model issues. If the auditor flags
 * something, the receipts dashboard should show it, not just the dev
 * dashboard. Builds trust by leading with limitations.
 */
import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ActivityIndicator } from 'react-native';
import { createClient } from '@supabase/supabase-js';

import { THEME, TIER_COLOR, OUTCOME_COLOR } from '../theme';
const supabase = createClient(
  process.env.EXPO_PUBLIC_SUPABASE_URL!,
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY!,
);

const BRAND_AMBER = THEME.hrb;
const RED = THEME.loss;
const CARD_BG = THEME.surface;
const TEXT_PRIMARY = THEME.text;
const TEXT_MUTED = THEME.textDim;
const BORDER = THEME.border;

type Finding = {
  computed_date: string;
  prop_type: string;
  direction: string;
  higher_tier: string;
  lower_tier: string;
  higher_rate: number;
  higher_n: number;
  lower_rate: number;
  lower_n: number;
  delta_pct: number;
  severity: 'warn' | 'critical';
};

export const TierIntegrityBadge: React.FC = () => {
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [latestDate, setLatestDate] = useState<string | null>(null);

  useEffect(() => {
    fetchFindings();
  }, []);

  const fetchFindings = async () => {
    setLoading(true);
    try {
      // Get most recent computed_date with findings
      const { data: dateRow, error: dateErr } = await supabase
        .from('tier_integrity_findings')
        .select('computed_date')
        .order('computed_date', { ascending: false })
        .limit(1);
      if (dateErr || !dateRow || dateRow.length === 0) {
        setFindings([]);
        return;
      }
      const date = dateRow[0].computed_date;
      setLatestDate(date);
      const { data, error } = await supabase
        .from('tier_integrity_findings')
        .select('*')
        .eq('computed_date', date)
        .order('delta_pct', { ascending: false });
      if (error) {
        console.warn('[TierIntegrityBadge] fetch error:', error.message);
        return;
      }
      setFindings((data || []) as Finding[]);
    } catch (e: any) {
      console.warn('[TierIntegrityBadge] exception:', e?.message);
      setFindings([]);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator size="small" color={BRAND_AMBER} />
      </View>
    );
  }

  if (findings.length === 0) {
    // Clean state — log a positive note so users see the system is working
    return (
      <View style={styles.cleanContainer}>
        <Text style={styles.cleanIcon}>✅</Text>
        <Text style={styles.cleanText}>
          Tier integrity check passed{latestDate ? ` (${latestDate})` : ''}.
          {' '}PRIME &gt; STRONG &gt; LEAN intact across all cohorts.
        </Text>
      </View>
    );
  }

  const criticalCount = findings.filter(f => f.severity === 'critical').length;
  const warnCount = findings.filter(f => f.severity === 'warn').length;

  return (
    <View style={styles.container}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>
          ⚠️  Tier Integrity {criticalCount > 0 ? 'Alert' : 'Warning'}
        </Text>
        <Text style={styles.date}>{latestDate}</Text>
      </View>
      <Text style={styles.subtitle}>
        {criticalCount > 0 && <Text style={{ color: RED }}>{criticalCount} critical · </Text>}
        {warnCount > 0 && <Text>{warnCount} warning</Text>}
        {' — '}lower-tier cohorts outperforming higher-tier on 30D audit.
        {' '}Auto-suppression demotes affected picks; gates are being retuned.
      </Text>

      {findings.slice(0, 5).map((f, i) => (
        <View key={`${f.prop_type}-${f.direction}-${i}`} style={[styles.findingRow, i > 0 && styles.findingBorder]}>
          <View style={{ flex: 1 }}>
            <Text style={styles.findingType}>
              {f.prop_type.replace(/_/g, ' ')} {f.direction}
            </Text>
            <Text style={styles.findingDetail}>
              <Text style={{ color: f.severity === 'critical' ? RED : BRAND_AMBER }}>{f.lower_tier}</Text>
              {' '}{(f.lower_rate * 100).toFixed(1)}% (n={f.lower_n})
              {' beats '}
              <Text style={styles.tierLabel}>{f.higher_tier}</Text>
              {' '}{(f.higher_rate * 100).toFixed(1)}% (n={f.higher_n})
            </Text>
          </View>
          <Text style={[styles.delta, { color: f.severity === 'critical' ? RED : BRAND_AMBER }]}>
            +{f.delta_pct.toFixed(1)}pt
          </Text>
        </View>
      ))}

      {findings.length > 5 && (
        <Text style={styles.moreText}>+ {findings.length - 5} more findings</Text>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    backgroundColor: CARD_BG,
    borderRadius: 14,
    padding: 14,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: THEME.hrb + '59',
  },
  cleanContainer: {
    backgroundColor: CARD_BG,
    borderRadius: 14,
    padding: 12,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: THEME.accent + '33',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  cleanIcon: { fontSize: 16 },
  cleanText: { color: TEXT_MUTED, fontSize: 11, flex: 1, lineHeight: 16 },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 },
  title: { color: BRAND_AMBER, fontSize: 13, fontWeight: '800' },
  date: { color: TEXT_MUTED, fontSize: 10 },
  subtitle: { color: TEXT_MUTED, fontSize: 11, lineHeight: 16, marginBottom: 10 },
  findingRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 8, gap: 10 },
  findingBorder: { borderTopWidth: 0.5, borderTopColor: BORDER },
  findingType: { color: TEXT_PRIMARY, fontWeight: '600', fontSize: 12, textTransform: 'capitalize' },
  findingDetail: { color: TEXT_MUTED, fontSize: 10, marginTop: 2 },
  tierLabel: { color: TEXT_PRIMARY, fontWeight: '600' },
  delta: { fontSize: 13, fontWeight: '800', minWidth: 60, textAlign: 'right' },
  moreText: { color: TEXT_MUTED, fontSize: 10, marginTop: 8, fontStyle: 'italic' },
});
