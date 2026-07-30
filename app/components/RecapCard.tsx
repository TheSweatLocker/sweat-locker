/**
 * RecapCard — sport-aware receipts banner.
 *
 * Shows the most recent recap for ACTIVE sport with cadence-aware framing:
 *   - MLB/NBA/NCAAB/NHL: "Yesterday's Card"
 *   - NFL: "Last Slate" or "Week N"
 *   - UFC: "Last Event"
 *
 * Pulls from existing per-sport tables:
 *   - daily_best_bet_history (POTD, multi-sport via 'sport' column)
 *   - daily_dawg (DotD)
 *   - {sport}_pipeline_props (props with results)
 *
 * For sports where the pipeline hasn't shipped data yet (NBA, NFL, NHL
 * in v1.0), shows a "Season launches [date]" placeholder rather than zeros.
 *
 * Tap → expanded modal with per-pick breakdown (v1.1; for now just the
 * summary card).
 */
import React, { useEffect, useState } from 'react';
import { View, Text, TouchableOpacity, ActivityIndicator, StyleSheet, ScrollView } from 'react-native';
import { createClient } from '@supabase/supabase-js';
import { THEME, TIER_COLOR, OUTCOME_COLOR } from '../theme';
import {
  Sport, Period, getDefaultPeriod, getAvailablePeriods,
  getDateRange, sportDb, getResolvedPropsTable, isSportLive,
} from '../lib/sportPeriods';

const supabase = createClient(
  process.env.EXPO_PUBLIC_SUPABASE_URL!,
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY!,
);

const BRAND_GREEN = '#00e5a0';
const BRAND_AMBER = THEME.hrb;
const CARD_BG = THEME.surface;
const TEXT_PRIMARY = THEME.text;
const TEXT_MUTED = THEME.textDim;
const BORDER = THEME.border;
const RED = '#ff4d6d';

type RecapData = {
  potd: { lean: string; result: string | null; matchup?: string } | null;
  dawg: { team: string; result?: string | null; matchup?: string } | null;
  primeProps: { wins: number; losses: number; pushes: number };
  strongProps: { wins: number; losses: number; pushes: number };
  totalGames: number;
};

type Props = {
  sport: Sport;
  initialPeriod?: Period;
  onTap?: () => void;  // Open full breakdown modal (v1.1)
};

export const RecapCard: React.FC<Props> = ({ sport, initialPeriod, onTap }) => {
  const [period, setPeriod] = useState<Period>(initialPeriod ?? getDefaultPeriod(sport));
  const [data, setData] = useState<RecapData | null>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState(getDateRange(sport, period));

  // Re-fetch when sport or period changes
  useEffect(() => {
    setRange(getDateRange(sport, period));
  }, [sport, period]);

  useEffect(() => {
    if (!isSportLive(sport)) {
      setLoading(false);
      return;
    }
    fetchRecap();
  }, [sport, range.startDate, range.endDate]);

  const fetchRecap = async () => {
    setLoading(true);
    try {
      const sportLower = sportDb(sport);
      const [potdRes, dawgRes, propsResult] = await Promise.all([
        // POTD — daily_best_bet_history has multi-sport via `sport` column.
        // Pull all rows in range, take the most recent for "yesterday" or aggregate for ranges.
        supabase
          .from('daily_best_bet_history')
          .select('bet_date, game, lean, result, sport')
          .eq('sport', sportLower.toUpperCase())   // POTD table uses uppercase
          .gte('bet_date', range.startDate)
          .lte('bet_date', range.endDate)
          .order('bet_date', { ascending: false }),
        // DotD — daily_dawg has game_date
        supabase
          .from('daily_dawg')
          .select('game_date, team, matchup, result, tier, conviction')
          .gte('game_date', range.startDate)
          .lte('game_date', range.endDate)
          .order('game_date', { ascending: false }),
        // Props — only fetch if this sport has a pipeline_props table
        fetchProps(sport, range.startDate, range.endDate),
      ]);

      // For "yesterday" period, surface single POTD/DotD. For ranges, surface aggregates.
      const potdRows = potdRes.data || [];
      const dawgRows = dawgRes.data || [];
      const propRows = propsResult || [];

      let potd = null;
      if (period === 'yesterday' && potdRows[0]) {
        potd = {
          lean: potdRows[0].lean,
          result: potdRows[0].result,
          matchup: potdRows[0].game,
        };
      } else if (potdRows.length > 0) {
        // For aggregate periods, summarize POTD W-L
        const w = potdRows.filter(p => p.result === 'Win').length;
        const l = potdRows.filter(p => p.result === 'Loss').length;
        potd = { lean: `${w}-${l}`, result: null, matchup: `${potdRows.length} POTDs` };
      }

      let dawg = null;
      if (period === 'yesterday' && dawgRows[0]) {
        dawg = {
          team: dawgRows[0].team,
          result: dawgRows[0].result,
          matchup: dawgRows[0].matchup,
        };
      } else if (dawgRows.length > 0) {
        const w = dawgRows.filter(d => d.result === 'Win').length;
        const l = dawgRows.filter(d => d.result === 'Loss').length;
        dawg = { team: `${w}-${l}`, result: null, matchup: `${dawgRows.length} DotDs` };
      }

      const primeProps = countTier(propRows, 'PRIME');
      const strongProps = countTier(propRows, 'STRONG');

      setData({
        potd,
        dawg,
        primeProps,
        strongProps,
        totalGames: countResolvedGames(potdRows, dawgRows, propRows),
      });
    } catch (e: any) {
      console.warn('[RecapCard] fetch failed:', e?.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  // ── Off-season / not-yet-launched placeholder ──
  if (!isSportLive(sport)) {
    return (
      <View style={styles.card}>
        <Text style={styles.label}>{sport} RECEIPTS</Text>
        <Text style={styles.offseasonHeading}>Season launching soon</Text>
        <Text style={styles.offseasonSub}>
          {sport} pipeline activates when the season starts. We'll have receipts
          flowing live from day one.
        </Text>
      </View>
    );
  }

  if (loading) {
    return (
      <View style={styles.card}>
        <ActivityIndicator size="small" color={BRAND_GREEN} />
      </View>
    );
  }

  // No data found — could be a true zero day or a data lag
  if (!data || (!data.potd && !data.dawg && data.primeProps.wins + data.primeProps.losses === 0)) {
    return (
      <View style={styles.card}>
        <Text style={styles.label}>{range.label.toUpperCase()} — {sport}</Text>
        <Text style={styles.emptyText}>No resolved picks yet for this period.</Text>
      </View>
    );
  }

  const totalProps = data.primeProps.wins + data.primeProps.losses + data.strongProps.wins + data.strongProps.losses;
  const totalWins = data.primeProps.wins + data.strongProps.wins;
  const hitRate = totalProps > 0 ? Math.round((totalWins / totalProps) * 100) : 0;

  return (
    <TouchableOpacity activeOpacity={onTap ? 0.85 : 1} onPress={onTap} style={styles.card}>
      <View style={styles.headerRow}>
        <Text style={styles.label}>{range.label.toUpperCase()} — {sport}</Text>
        {onTap && <Text style={styles.tapHint}>tap for details ›</Text>}
      </View>

      {/* POTD row */}
      {data.potd && (
        <Row
          icon="🏆"
          title="POTD"
          subtitle={data.potd.matchup || data.potd.lean}
          result={data.potd.result}
        />
      )}

      {/* DotD row */}
      {data.dawg && (
        <Row
          icon="🐕"
          title="DotD"
          subtitle={`${data.dawg.team}${data.dawg.matchup ? ' — ' + data.dawg.matchup : ''}`}
          result={data.dawg.result}
        />
      )}

      {/* Prop tier breakdown */}
      {totalProps > 0 && (
        <View style={styles.tierBox}>
          <View style={styles.tierRow}>
            <Text style={styles.tierLabel}>🔒 PRIME PROPS</Text>
            <View style={styles.tierStats}>
              <Text style={styles.tierStat}>{data.primeProps.wins}-{data.primeProps.losses}</Text>
              <PctBadge wins={data.primeProps.wins} losses={data.primeProps.losses} />
            </View>
          </View>
          <View style={styles.tierRow}>
            <Text style={styles.tierLabel}>⚡ STRONG PROPS</Text>
            <View style={styles.tierStats}>
              <Text style={styles.tierStat}>{data.strongProps.wins}-{data.strongProps.losses}</Text>
              <PctBadge wins={data.strongProps.wins} losses={data.strongProps.losses} />
            </View>
          </View>
          <View style={[styles.tierRow, styles.tierTotal]}>
            <Text style={[styles.tierLabel, styles.tierTotalLabel]}>TOTAL</Text>
            <View style={styles.tierStats}>
              <Text style={[styles.tierStat, styles.tierTotalStat]}>
                {totalWins}-{totalProps - totalWins}
              </Text>
              <Text style={[styles.tierPct, hitRate >= 60 ? styles.greenText : hitRate >= 50 ? styles.amberText : styles.redText]}>
                {hitRate}%
              </Text>
            </View>
          </View>
        </View>
      )}

      {/* Period selector chips */}
      {getAvailablePeriods(sport).length > 1 && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.chipScroll}
          contentContainerStyle={styles.chipScrollContent}
        >
          {getAvailablePeriods(sport).map(p => (
            <TouchableOpacity
              key={p}
              onPress={() => setPeriod(p)}
              style={[styles.chip, period === p && styles.chipActive]}
            >
              <Text style={[styles.chipText, period === p && styles.chipTextActive]}>
                {periodLabel(sport, p)}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      )}
    </TouchableOpacity>
  );
};

// ── Helpers ──

async function fetchProps(sport: Sport, startDate: string, endDate: string) {
  const table = getResolvedPropsTable(sport);
  if (!table) return [];
  const { data, error } = await supabase
    .from(table)
    .select('tier, result, prop_type')
    .gte('game_date', startDate)
    .lte('game_date', endDate)
    .in('tier', ['PRIME', 'STRONG'])
    .in('result', ['Win', 'Loss', 'Push']);
  if (error) {
    console.warn('[RecapCard] props fetch error:', error.message);
    return [];
  }
  return data || [];
}

function countTier(rows: any[], tier: string) {
  const filt = rows.filter(r => r.tier === tier);
  return {
    wins: filt.filter(r => r.result === 'Win').length,
    losses: filt.filter(r => r.result === 'Loss').length,
    pushes: filt.filter(r => r.result === 'Push').length,
  };
}

function countResolvedGames(potd: any[], dawg: any[], props: any[]) {
  const dates = new Set([
    ...potd.map((p: any) => p.bet_date),
    ...dawg.map((d: any) => d.game_date),
    ...props.map((p: any) => p.game_date),
  ]);
  return dates.size;
}

function periodLabel(sport: Sport, p: Period): string {
  if (p === 'yesterday') return 'Yesterday';
  if (p === 'last_slate') return 'Last Slate';
  if (p === 'last_event') return 'Last Event';
  if (p === 'last_7d') return 'Last 7D';
  if (p === 'last_30d') return 'Last 30D';
  if (p === 'season') return 'Season';
  return p;
}

const Row: React.FC<{ icon: string; title: string; subtitle: string; result: string | null }> = ({ icon, title, subtitle, result }) => {
  const isWin = result === 'Win';
  const isLoss = result === 'Loss';
  return (
    <View style={styles.row}>
      <Text style={styles.rowIcon}>{icon}</Text>
      <View style={{ flex: 1 }}>
        <Text style={styles.rowTitle}>{title}</Text>
        <Text style={styles.rowSub} numberOfLines={1}>{subtitle}</Text>
      </View>
      {result && (
        <View style={[styles.resultBadge, isWin && styles.resultWin, isLoss && styles.resultLoss]}>
          <Text style={[styles.resultText, isWin && styles.resultTextWin, isLoss && styles.resultTextLoss]}>
            {isWin ? '✓ WIN' : isLoss ? '✗ LOSS' : (result?.toUpperCase() || 'PEND')}
          </Text>
        </View>
      )}
    </View>
  );
};

const PctBadge: React.FC<{ wins: number; losses: number }> = ({ wins, losses }) => {
  const n = wins + losses;
  if (n === 0) return null;
  const pct = Math.round((wins / n) * 100);
  const color = pct >= 60 ? BRAND_GREEN : pct >= 50 ? BRAND_AMBER : RED;
  return <Text style={[styles.tierPct, { color }]}>{pct}%</Text>;
};

const styles = StyleSheet.create({
  card: { backgroundColor: CARD_BG, borderRadius: 14, padding: 16, marginBottom: 14, borderWidth: 1, borderColor: BORDER },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  label: { color: TEXT_MUTED, fontSize: 10, fontWeight: '800', letterSpacing: 1.2 },
  tapHint: { color: TEXT_MUTED, fontSize: 10 },
  offseasonHeading: { color: TEXT_PRIMARY, fontWeight: '700', fontSize: 15, marginTop: 4, marginBottom: 6 },
  offseasonSub: { color: TEXT_MUTED, fontSize: 12, lineHeight: 17 },
  emptyText: { color: TEXT_MUTED, fontSize: 13 },
  row: { flexDirection: 'row', alignItems: 'center', paddingVertical: 6 },
  rowIcon: { fontSize: 18, marginRight: 10, width: 26 },
  rowTitle: { color: TEXT_PRIMARY, fontWeight: '700', fontSize: 13 },
  rowSub: { color: TEXT_MUTED, fontSize: 11, marginTop: 1 },
  resultBadge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 5, backgroundColor: 'rgba(122,146,168,0.15)' },
  resultWin: { backgroundColor: 'rgba(0,229,160,0.15)' },
  resultLoss: { backgroundColor: 'rgba(255,77,109,0.15)' },
  resultText: { color: TEXT_MUTED, fontSize: 10, fontWeight: '800', letterSpacing: 0.5 },
  resultTextWin: { color: BRAND_GREEN },
  resultTextLoss: { color: RED },
  tierBox: { marginTop: 12, paddingTop: 10, borderTopWidth: 0.5, borderTopColor: BORDER },
  tierRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 4 },
  tierLabel: { color: TEXT_MUTED, fontSize: 11, fontWeight: '700' },
  tierStats: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  tierStat: { color: TEXT_PRIMARY, fontSize: 12, fontWeight: '700', minWidth: 40, textAlign: 'right' },
  tierPct: { fontSize: 12, fontWeight: '800', minWidth: 40, textAlign: 'right' },
  tierTotal: { marginTop: 4, paddingTop: 6, borderTopWidth: 0.5, borderTopColor: BORDER },
  tierTotalLabel: { color: TEXT_PRIMARY, fontWeight: '800' },
  tierTotalStat: { fontSize: 14 },
  greenText: { color: BRAND_GREEN },
  amberText: { color: BRAND_AMBER },
  redText: { color: RED },
  chipScroll: { marginTop: 12 },
  chipScrollContent: { gap: 6 },
  chip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 14, backgroundColor: 'rgba(122,146,168,0.10)', borderWidth: 1, borderColor: 'transparent' },
  chipActive: { backgroundColor: 'rgba(0,229,160,0.10)', borderColor: BRAND_GREEN },
  chipText: { color: TEXT_MUTED, fontSize: 11, fontWeight: '600' },
  chipTextActive: { color: BRAND_GREEN, fontWeight: '700' },
});
