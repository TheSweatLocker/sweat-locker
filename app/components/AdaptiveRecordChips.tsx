/**
 * AdaptiveRecordChips — top-3 in-season sports by L30D units.
 *
 * 2026-09-14 · v1.0.1 #4 (adaptive record header, option C).
 *
 * Andy's spec (project_home_screen_hot_streak_907):
 * > Current state: Hardcoded "MLB 59% L30D" at top of home. Breaks when
 * > MLB goes off-season (Oct 31), another sport is doing better, or
 * > multi-sport combined figure is more accurate.
 * >
 * > Recommended: option C — Show top 2-3 sports as small chips
 * > `NFL 68% · MLB 55% · NCAAF 62%`. Best of both worlds — visible
 * > per-sport but not one hardcoded champion.
 *
 * Renders:
 *   NFL 68% · +14u   |   MLB 61% · +12u   |   NCAAF 55% · +3u
 *
 * Data path: reads `surface_records` where surface='sharp_card' and
 * sport is in-season, filters to those with sample_n >= 10, sorts by
 * units_net desc, takes top 3.
 *
 * Silent-hide when zero sports meet the sample floor (early-season or
 * offseason wall). The "SIDES + PROPS · ALL-TIME" card below still shows
 * the unified all-sport record — this chip row is per-sport context, not
 * a replacement.
 *
 * Wire-up: <AdaptiveRecordChips /> renders itself; parent doesn't need
 * to prop-drill records. Consumers put it just above the existing
 * sharpRecord render block in the home tab.
 */
import React from 'react';
import {View, Text} from 'react-native';

type SR = {
  sport: string;
  wins: number;
  losses: number;
  pushes: number;
  units_net: number;
  window_key?: string;
  sample_n?: number;
};

type Props = {
  /** Function that returns surface_records for last 30 days, per sport.
   *  Should query `surface_records` where surface='sharp_card' and
   *  window_key='30d' (or equivalent), returning one row per sport. */
  fetcher: () => Promise<SR[]>;
  /** Color tokens from parent theme (matches existing THEME shape). */
  theme: {
    accent: string;
    win: string;
    loss: string;
    text: string;
    textMuted: string;
    textDim: string;
    border: string;
    cardBg?: string;
  };
  /** Minimum picks in window to surface a sport. Default 10 per memory spec. */
  minSample?: number;
  /** How many chips to show (default 3). */
  maxChips?: number;
};

const SPORT_ABBREV: Record<string, string> = {
  NFL: 'NFL', MLB: 'MLB', NCAAF: 'NCAAF', NBA: 'NBA',
  NHL: 'NHL', NCAAB: 'NCAAB', UFC: 'UFC',
};

export function AdaptiveRecordChips({fetcher, theme, minSample = 10, maxChips = 3}: Props): React.ReactElement | null {
  const [rows, setRows] = React.useState<SR[] | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetcher();
        if (cancelled) return;
        setRows(Array.isArray(data) ? data : []);
      } catch {
        if (cancelled) return;
        setRows([]);
      }
    })();
    return () => { cancelled = true; };
  }, [fetcher]);

  if (rows === null) return null;  // initial load — render nothing (avoid flash)

  // Filter to sports with meaningful sample + rank by units.
  const eligible = rows
    .filter((r) => {
      const total = (r.wins || 0) + (r.losses || 0);
      const n = r.sample_n ?? total;
      return n >= minSample;
    })
    .sort((a, b) => (b.units_net || 0) - (a.units_net || 0))
    .slice(0, maxChips);

  if (eligible.length === 0) return null;  // silent hide when no sport qualifies

  return (
    <View style={{flexDirection: 'row', flexWrap: 'wrap', gap: 8, paddingHorizontal: 12, marginTop: 4, marginBottom: 8, justifyContent: 'center'}}>
      {eligible.map((r) => {
        const total = (r.wins || 0) + (r.losses || 0);
        const hitPct = total > 0 ? Math.round(1000 * (r.wins || 0) / total) / 10 : 0;
        const unitsColor = (r.units_net || 0) > 0 ? theme.win : (r.units_net || 0) < 0 ? theme.loss : theme.textMuted;
        const label = SPORT_ABBREV[r.sport?.toUpperCase()] || r.sport;
        return (
          <View
            key={r.sport}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: 5,
              paddingVertical: 5,
              paddingHorizontal: 10,
              borderRadius: 999,
              backgroundColor: theme.cardBg || (theme.text + '08'),
              borderWidth: 0.75,
              borderColor: theme.border + '77',
            }}
          >
            <Text style={{color: theme.accent, fontSize: 11, fontWeight: '800', letterSpacing: 0.4}}>
              {label}
            </Text>
            <Text style={{color: theme.textMuted, fontSize: 10, fontWeight: '600'}}>
              {total > 0 ? `${hitPct}%` : '—'}
            </Text>
            <Text style={{color: unitsColor, fontSize: 10, fontWeight: '800', fontVariant: ['tabular-nums']}}>
              {(r.units_net || 0) >= 0 ? '+' : ''}{Number(r.units_net || 0).toFixed(1)}u
            </Text>
          </View>
        );
      })}
      <View style={{width: '100%', alignItems: 'center', marginTop: 4}}>
        <Text style={{color: theme.textDim, fontSize: 9, fontWeight: '700', letterSpacing: 0.4}}>
          L30D · THE SHARP
        </Text>
      </View>
    </View>
  );
}
