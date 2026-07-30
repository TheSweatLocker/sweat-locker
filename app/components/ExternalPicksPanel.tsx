/**
 * ExternalPicksPanel — per-game aggregation of public handicapper picks.
 *
 * Backend: pull_externals_mlb.py writes to external_picks table twice daily
 * (noon + 5pm ET) from 7+ sources (Action, Dimers, Covers, Pickswise,
 * PickDawgz, VSiN, BettingPros, ...). This panel reads the picks for a
 * single game and surfaces them with source attribution + audit-derived
 * fade/boost/neutral flags.
 *
 * UX principles:
 *   - Attribution first (nominative fair use — name-drop the source)
 *   - Colored flag per audit finding (boost=green, fade=red, trust=blue)
 *   - Grouped by side agreement to show consensus vs contrarian
 *   - Empty state = "No public reads pulled yet" (never "no data" — sounds broken)
 */
import React, { useEffect, useState } from 'react';
import { View, Text, ActivityIndicator, StyleSheet, Linking, TouchableOpacity } from 'react-native';
import { createClient } from '@supabase/supabase-js';

import { THEME, TIER_COLOR, OUTCOME_COLOR } from '../theme';
const supabase = createClient(
  process.env.EXPO_PUBLIC_SUPABASE_URL!,
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY!,
);

const CARD_BG = THEME.surface;
const BORDER = THEME.border;
const TEXT_PRIMARY = THEME.text;
const TEXT_MUTED = THEME.textDim;
const BOOST = '#00e5a0';   // green — aligns with audit
const FADE = '#ff4d6d';    // red — audit-flagged counterindicator
const TRUST = '#5bb9ff';   // blue — trusted but not headline
const NEUTRAL = THEME.textDim; // gray — reference-only

type ExternalPick = {
  pick_id?: string;
  game_id: string;
  source: string;
  surface: string;
  pick_side: string | null;
  pick_line: number | null;
  odds_american: number | null;
  confidence: string | null;
  raw_text: string | null;
  source_url: string | null;
  fade_flag: string | null;
};

type SourceCalibration = {
  source: string;
  surface: string;
  hit_pct: number | null;
  sample_n: number;
  wins: number;
  losses: number;
};

const SOURCE_LABEL: Record<string, string> = {
  action: 'Action Network',
  dimers: 'Dimers',
  covers: 'Covers',
  vsin: 'VSiN',
  pickswise: 'Pickswise',
  pickdawgz: 'PickDawgz',
  bettingpros: 'BettingPros',
  docsports: 'Doc Sports',
  cbs: 'CBS Sports',
  oddsshark: 'OddsShark',
  fangraphs: 'Fangraphs',
  ballparkpal: 'Ballpark Pal',
  scp: 'Sports Chat Place',
};

const flagColor = (flag: string | null): string => {
  if (flag === 'boost') return BOOST;
  if (flag === 'fade') return FADE;
  if (flag === 'trust') return TRUST;
  return NEUTRAL;
};

const flagLabel = (flag: string | null): string => {
  if (flag === 'boost') return 'BOOST';
  if (flag === 'fade') return 'FADE';
  if (flag === 'trust') return 'TRUST';
  return 'REF';
};

const surfaceLabel = (surface: string): string => {
  if (surface === 'ml') return 'ML';
  if (surface === 'total') return 'Total';
  if (surface === 'rl') return 'RL';
  if (surface === 'prop') return 'Prop';
  return surface.toUpperCase();
};

type Props = { gameId: string };

export const ExternalPicksPanel: React.FC<Props> = ({ gameId }) => {
  const [picks, setPicks] = useState<ExternalPick[] | null>(null);
  const [calMap, setCalMap] = useState<Record<string, SourceCalibration>>({});
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data } = await supabase
        .from('external_picks')
        .select('*')
        .eq('game_id', gameId)
        .order('fetched_at', { ascending: false });
      if (cancelled) return;
      setPicks(data || []);

      // Also fetch 30d rolling calibration for each source × surface
      // present in this game's picks. Keyed by `${source}::${surface}`
      // so a single lookup surfaces the right hit rate.
      if (data && data.length) {
        const { data: cal } = await supabase
          .from('external_source_calibration')
          .select('source,surface,hit_pct,sample_n,wins,losses,computed_date')
          .eq('window_label', '30d')
          .order('computed_date', { ascending: false })
          .limit(200);
        if (cancelled) return;
        const map: Record<string, SourceCalibration> = {};
        (cal || []).forEach((row: any) => {
          const key = `${row.source}::${row.surface}`;
          if (!map[key]) map[key] = row;   // latest computed_date wins
        });
        setCalMap(map);
      }
    })();
    return () => { cancelled = true; };
  }, [gameId]);

  if (picks === null) {
    return (
      <View style={styles.card}>
        <ActivityIndicator color={TEXT_MUTED} />
      </View>
    );
  }

  if (picks.length === 0) {
    return (
      <View style={styles.card}>
        <Text style={styles.title}>What Others Are Saying</Text>
        <Text style={styles.emptyText}>
          No public reads pulled yet for this game.
        </Text>
      </View>
    );
  }

  // Consensus tally: which side is the aggregated public on?
  const sideTally: Record<string, number> = {};
  picks.forEach(p => {
    if (p.pick_side) sideTally[p.pick_side] = (sideTally[p.pick_side] || 0) + 1;
  });
  const consensusSide = Object.entries(sideTally)
    .sort((a, b) => b[1] - a[1])[0];

  const shown = expanded ? picks : picks.slice(0, 4);

  return (
    <View style={styles.card}>
      <View style={styles.headerRow}>
        <View>
          <Text style={styles.title}>What Others Are Saying</Text>
          <Text style={styles.subtitle}>
            {picks.length} pick{picks.length !== 1 ? 's' : ''} from{' '}
            {new Set(picks.map(p => p.source)).size} sources
          </Text>
        </View>
        {consensusSide && (
          <View style={styles.consensusBadge}>
            <Text style={styles.consensusPct}>
              {Math.round((consensusSide[1] / picks.length) * 100)}%
            </Text>
            <Text style={styles.consensusLabel}>on {consensusSide[0]}</Text>
          </View>
        )}
      </View>

      <View style={{ marginTop: 12 }}>
        {shown.map((p, i) => {
          const cal = calMap[`${p.source}::${p.surface}`];
          const hasCal = cal && cal.sample_n >= 5;
          // Hit-rate color: green >= 55%, red < 45%, gray between
          const calColor = hasCal
            ? (cal!.hit_pct! >= 55 ? BOOST : cal!.hit_pct! < 45 ? FADE : NEUTRAL)
            : NEUTRAL;
          return (
          <View key={i} style={styles.pickRow}>
            <View style={{ flex: 1 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Text style={styles.sourceLabel}>
                  {SOURCE_LABEL[p.source] || p.source}
                </Text>
                <View style={[styles.flagChip, { borderColor: flagColor(p.fade_flag), backgroundColor: flagColor(p.fade_flag) + '18' }]}>
                  <Text style={[styles.flagText, { color: flagColor(p.fade_flag) }]}>
                    {flagLabel(p.fade_flag)}
                  </Text>
                </View>
                <Text style={styles.surfaceText}>{surfaceLabel(p.surface)}</Text>
                {hasCal && (
                  <Text style={[styles.calText, { color: calColor }]}>
                    · {cal!.hit_pct!.toFixed(0)}% L30 (n={cal!.sample_n})
                  </Text>
                )}
              </View>
              <Text style={styles.pickText}>
                {p.pick_side || '—'}
                {p.pick_line != null ? ` ${p.pick_line}` : ''}
                {p.odds_american != null ? `  ${p.odds_american > 0 ? '+' : ''}${p.odds_american}` : ''}
                {p.confidence ? `  · ${p.confidence}` : ''}
              </Text>
            </View>
            {p.source_url && (
              <TouchableOpacity onPress={() => Linking.openURL(p.source_url!)}>
                <Text style={styles.linkText}>↗</Text>
              </TouchableOpacity>
            )}
          </View>
          );
        })}
      </View>

      {picks.length > 4 && (
        <TouchableOpacity onPress={() => setExpanded(!expanded)} style={styles.moreBtn}>
          <Text style={styles.moreText}>
            {expanded ? 'Show less' : `Show ${picks.length - 4} more`}
          </Text>
        </TouchableOpacity>
      )}

      <Text style={styles.disclaimer}>
        Picks aggregated from public handicapper pages. BOOST/FADE flags based
        on our 30-day source performance audit. Sources are third-party; not
        affiliated.
      </Text>
    </View>
  );
};

const styles = StyleSheet.create({
  card: {
    backgroundColor: CARD_BG,
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: BORDER,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
  },
  title: { color: TEXT_PRIMARY, fontWeight: '800', fontSize: 16 },
  subtitle: { color: TEXT_MUTED, fontSize: 11, marginTop: 2 },
  consensusBadge: {
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 8,
    backgroundColor: '#0a1018',
    borderWidth: 1,
    borderColor: BORDER,
  },
  consensusPct: { color: TEXT_PRIMARY, fontWeight: '800', fontSize: 15 },
  consensusLabel: { color: TEXT_MUTED, fontSize: 9, marginTop: 1 },
  pickRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: BORDER,
  },
  sourceLabel: { color: TEXT_PRIMARY, fontWeight: '700', fontSize: 13 },
  flagChip: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    borderWidth: 1,
  },
  flagText: { fontSize: 9, fontWeight: '800' },
  surfaceText: { color: TEXT_MUTED, fontSize: 10 },
  calText: { fontSize: 10, fontWeight: '600' },
  pickText: { color: TEXT_PRIMARY, fontSize: 13, marginTop: 4 },
  linkText: { color: TRUST, fontSize: 18, paddingHorizontal: 8 },
  moreBtn: { paddingVertical: 10, alignItems: 'center' },
  moreText: { color: TRUST, fontSize: 12, fontWeight: '600' },
  disclaimer: {
    color: TEXT_MUTED,
    fontSize: 9,
    marginTop: 10,
    lineHeight: 13,
    fontStyle: 'italic',
  },
});

export default ExternalPicksPanel;
