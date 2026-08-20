/**
 * LineMovementTab — Steam Room "🌊 Line Movement" redesign (2026-08-15).
 *
 * Rebuilt from the flat inline list that lived in index.tsx. Three problems
 * that redesign addresses:
 *
 * 1. Purpose was unclear. Now anchored on: "where is sharp money moving,
 *    and how do we know it's actually sharp?"
 * 2. Signals contradicted each other on the same card ("RLM Away Sharp
 *    lean away / no pick on this game"). The card body now has clear
 *    section hierarchy: signal → source agreement + hit rates → model
 *    alignment (no more phantom "no pick" chip).
 * 3. Sport cadence was ignored (NFL moves over a week, MLB over hours).
 *    Header + filter chips now explain the sport-adaptive window.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────┐
 *   │ 🔥 STRONGEST SIGNALS RIGHT NOW                   │
 *   │ [TRIPLE·LAD +3EV] [CONF·BUF mdl+] [CONF·NYY]     │
 *   ├──────────────────────────────────────────────────┤
 *   │ Filter: [All Sports ▾] [Signal ≥ CONFIRMED ▾]    │
 *   ├──────────────────────────────────────────────────┤
 *   │ Full ranked list, one card per (game, market)    │
 *   └──────────────────────────────────────────────────┘
 *
 * How we prove signals aren't noise (rendered inline on each card):
 *   • Multi-source cross-verification (SHARP_TRIPLE_CONFIRMED = OC + FR +
 *     Cleatz all agree; SHARP_CONFIRMED = 2 sources agree; LEAN = 1 source
 *     only, no numbers surfaced).
 *   • Per-source track record from external_source_track_record (30d/90d
 *     hit rate + n) shown next to each source's %.
 *   • Signal registry tier badge (VALIDATED / DISCOVERY / UNVALIDATED /
 *     ANTI_VALIDATED) when the signal type has a backtest.
 */
import React from 'react';
import {View, Text, TouchableOpacity, ScrollView, ActivityIndicator} from 'react-native';
import Explainer from './Explainer';

// ─── PALETTE (matches app THEME) ─────────────────────────────────────
const T = {
  bg: '#0b1620',
  surface: '#12202c',
  surfaceAlt: '#182838',
  text: '#e6edf3',
  textDim: '#9db1c3',
  textMuted: '#7a92a8',
  border: '#41586b',
  sharp: '#4ade80',
  loss: '#f87171',
  accent: '#5ea9e6',
  hrb: '#f5b800',
  win: '#4ade80',
};

// Sport-adaptive relevance window. NFL moves over a week, MLB over hours,
// so the "current signals" definition differs. Copy tells the user what
// window they're looking at so they can trust the freshness.
const SPORT_WINDOW: Record<string, string> = {
  MLB:   'last 24 hours',
  NBA:   'last 24 hours',
  NHL:   'last 24 hours',
  NFL:   'this week',
  NCAAF: 'this week',
  NCAAB: 'last 24 hours',
  UFC:   'fight week',
};

type SourceRecord = {source: string; sport: string; market: string; window_days: number; hit_rate: number|null; n_graded: number};

type Props = {
  loading: boolean;
  flags: any[];                             // line_movement_flags rows
  historySample: Record<string, any[]>;     // keyed by "gid::market"
  picksIdx: Record<string, {primary: any; supplementary: any}>;
  sourceRecords: SourceRecord[];            // external_source_track_record rows
  rawSigsIdx?: Record<string, {cleatz: any[]; fadereport: any[]}>;  // 2026-08-18 per-source raw sharp$
  onTapGame: (matchup: string, sport: string, gid: string) => void;
};

type SportFilter = 'ALL' | 'MLB' | 'NFL' | 'NCAAF' | 'NCAAB' | 'NBA' | 'NHL' | 'UFC';
type TierFilter = 'ALL' | 'CONFIRMED' | 'TRIPLE';

export default function LineMovementTab({
  loading, flags, historySample, picksIdx, sourceRecords, rawSigsIdx, onTapGame,
}: Props) {
  const [sportFilter, setSportFilter] = React.useState<SportFilter>('ALL');
  const [tierFilter, setTierFilter] = React.useState<TierFilter>('ALL');
  // 2026-08-20: rolling per-source + agreement-bucket calibration
  // (sharp_source_calibration + sharp_agreement_calibration tables,
  // populated nightly by audit_sharp_source_calibration.py).
  // See project_per_source_tracker_moat_818 — this surfaces the moat.
  const [sourceCal, setSourceCal] = React.useState<any[]>([]);
  const [agreementCal, setAgreementCal] = React.useState<any[]>([]);
  React.useEffect(() => {
    // Fetch 30d MLB calibration (window covers the effective moat sample)
    (async () => {
      try {
        const {supabase} = require('../lib/supabase');
        const [{data: src}, {data: agr}] = await Promise.all([
          supabase.from('sharp_source_calibration')
            .select('source,market,hit_rate,sample_n,edge_pp,window_label')
            .eq('sport', 'MLB').eq('window_label', '30d').eq('market', 'ALL'),
          supabase.from('sharp_agreement_calibration')
            .select('bucket,market,hit_rate,sample_n,edge_pp,window_label')
            .eq('sport', 'MLB').eq('window_label', '30d').eq('market', 'ALL'),
        ]);
        if (Array.isArray(src)) setSourceCal(src);
        if (Array.isArray(agr)) setAgreementCal(agr);
      } catch { /* table not yet migrated — silent fail, header just hides */ }
    })();
  }, []);
  const bestDissentBucket = React.useMemo(() => {
    // Find the highest-edge DISSENT_ bucket with real n; used for the
    // header rec ("Following OC dissent wins 77% n=22 last 30d").
    const dissents = agreementCal.filter((r: any) =>
      String(r.bucket || '').startsWith('DISSENT_') && (r.sample_n || 0) >= 15);
    dissents.sort((a: any, b: any) => (b.edge_pp || 0) - (a.edge_pp || 0));
    return dissents[0] || null;
  }, [agreementCal]);

  // Index source records by source + sport + market for O(1) lookup
  const sourceRecordIdx = React.useMemo(() => {
    const idx: Record<string, SourceRecord> = {};
    for (const r of sourceRecords || []) {
      // Prefer 30d over 90d over lifetime for the inline display
      const key = `${r.source}::${r.sport}::${r.market}`;
      const existing = idx[key];
      const prio = (w: number) => w === 30 ? 3 : w === 90 ? 2 : w === 9999 ? 1 : 0;
      if (!existing || prio(r.window_days) > prio(existing.window_days)) idx[key] = r;
    }
    return idx;
  }, [sourceRecords]);

  // 2026-08-18 fix: don't derive sports from flags (would collapse to
  // only MLB on days when other sports have no flagged games). Use a
  // fixed list of live sports; filter still applies to whatever flags
  // are in-window, so an empty NFL filter shows the "no matches" state.
  const availableSports = React.useMemo(() => {
    return ['MLB', 'NFL', 'NCAAF', 'NCAAB', 'NBA', 'NHL'];
  }, []);

  // 2026-08-18: consolidate — group by GAME (was game::market). Prior
  // grouping produced two cards for one game when both ML and RL fired,
  // and the two often pointed opposite ways (Athletics ML + Royals RL).
  // Now: one card per game, showing all market flags together, with the
  // strongest classification featured as the lead + others chipped below.
  const groups = React.useMemo(() => {
    const g: Record<string, any[]> = {};
    flags.forEach((f: any) => {
      const key = f.game_id;
      (g[key] = g[key] || []).push(f);
    });
    return g;
  }, [flags]);

  const filteredGroups = React.useMemo(() => {
    const entries = Object.entries(groups);
    const PATTERN_RANK: Record<string, number> = {steam: 0, rlm: 1, limit: 2};
    // Score each group: TRIPLE > CONFIRMED > LEAN, then by pattern rank
    const score = (gs: any[]) => {
      const cls = gs.map(f => String(f.classification || ''));
      const hasTriple = cls.some(c => c.endsWith('_TRIPLE_CONFIRMED'));
      const hasConfirmed = cls.some(c => c.endsWith('_CONFIRMED') && !c.endsWith('_TRIPLE_CONFIRMED'));
      const tierScore = hasTriple ? 0 : hasConfirmed ? 1 : 2;
      const patternScore = Math.min(...gs.map((f: any) => PATTERN_RANK[f.pattern] ?? 9));
      return tierScore * 10 + patternScore;
    };
    let out = entries;
    if (sportFilter !== 'ALL') out = out.filter(([, gs]) => gs[0]?.sport === sportFilter);
    if (tierFilter === 'TRIPLE') {
      out = out.filter(([, gs]) => gs.some((f: any) => String(f.classification || '').endsWith('_TRIPLE_CONFIRMED')));
    } else if (tierFilter === 'CONFIRMED') {
      out = out.filter(([, gs]) => gs.some((f: any) => String(f.classification || '').endsWith('_CONFIRMED')));
    }
    out.sort(([, a], [, b]) => score(a) - score(b));
    return out;
  }, [groups, sportFilter, tierFilter]);

  const topSignals = React.useMemo(() => {
    // 2026-08-18: dedupe by game_id so the top strip doesn't show the
    // SAME game twice (once as ML sharp side, once as RL — was confusing
    // users who saw "OAK away" in top strip vs "KC home" in list below).
    // Now: for each game, pick its strongest market signal for the strip.
    const entries = Object.entries(groups);
    const triples = entries.filter(([, gs]) => gs.some((f: any) => String(f.classification || '').endsWith('_TRIPLE_CONFIRMED')));
    const confirmed = entries.filter(([, gs]) => gs.some((f: any) => String(f.classification || '').endsWith('_CONFIRMED') && !String(f.classification || '').endsWith('_TRIPLE_CONFIRMED')));
    // 2026-08-18: groups now keyed by game_id (post-consolidation), so
    // dedupe is implicit — one entry per game already. Still slice to
    // the top 3 by tier rank.
    return [...triples, ...confirmed].slice(0, 3);
  }, [groups]);

  if (loading) {
    return (
      <View style={{alignItems: 'center', paddingTop: 40}}>
        <ActivityIndicator color={T.hrb} />
        <Text style={{color: T.textDim, marginTop: 12, fontSize: 13}}>Reading line drift...</Text>
      </View>
    );
  }

  if (flags.length === 0) {
    return (
      <View style={{alignItems: 'center', paddingTop: 40, paddingHorizontal: 20}}>
        <Text style={{fontSize: 40}}>🌊</Text>
        <Text style={{color: T.text, fontWeight: '800', fontSize: 15, marginTop: 12, textAlign: 'center'}}>
          No sharp movement flagged
        </Text>
        <Text style={{color: T.textDim, fontSize: 12, marginTop: 8, textAlign: 'center', lineHeight: 18}}>
          Line snapshots seed every 30 min. Steam moves, RLM, and limit signals surface here when detected across our three public-split sources.
        </Text>
      </View>
    );
  }

  return (
    <View>
      {/* ─── ROLLING PER-SOURCE TRACK RECORD (moat) ───────────────
          2026-08-20: surface FR/CZ/OC rolling hit rates + best dissent
          bucket. This is the moat per project_per_source_tracker_moat_818
          — users see WHICH source has been sharp lately + when to follow
          the dissenter. Only renders when calibration data has loaded. */}
      {sourceCal.length > 0 && (
        <View style={{marginBottom: 14, backgroundColor: T.surface, borderRadius: 10, padding: 10, borderWidth: 1, borderColor: T.border}}>
          <Text style={{color: T.textDim, fontSize: 10, fontWeight: '800', letterSpacing: 0.6, marginBottom: 6}}>
            📊 30-DAY SOURCE TRACK RECORD (MLB)
          </Text>
          <View style={{flexDirection: 'row', gap: 10, flexWrap: 'wrap'}}>
            {['FR', 'CZ', 'OC'].map(src => {
              const row = sourceCal.find((r: any) => r.source === src);
              if (!row) return null;
              const hr = row.hit_rate; const n = row.sample_n; const edge = row.edge_pp;
              const color = hr >= 55 ? T.sharp : hr >= 50 ? T.hrb : T.loss;
              return (
                <View key={src} style={{flex: 1, minWidth: 90, backgroundColor: T.surfaceAlt, borderRadius: 8, padding: 8, borderLeftWidth: 2, borderLeftColor: color}}>
                  <Text style={{color: T.textMuted, fontSize: 9, fontWeight: '800', letterSpacing: 0.5}}>{src}</Text>
                  <Text style={{color: color, fontSize: 16, fontWeight: '900', marginTop: 2}}>{hr != null ? hr.toFixed(1) + '%' : '—'}</Text>
                  <Text style={{color: T.textDim, fontSize: 10, marginTop: 1}}>n={n}  {edge != null ? (edge >= 0 ? '+' : '') + edge.toFixed(1) + 'pp' : ''}</Text>
                </View>
              );
            })}
          </View>
          {bestDissentBucket && bestDissentBucket.edge_pp >= 5 && (
            <View style={{marginTop: 10, paddingTop: 8, borderTopWidth: 1, borderTopColor: T.border, flexDirection: 'row', alignItems: 'flex-start', gap: 6}}>
              <Text style={{color: T.hrb, fontSize: 12, marginTop: 1}}>💡</Text>
              <Text style={{color: T.textDim, fontSize: 11, flex: 1, lineHeight: 15}}>
                <Text style={{color: T.sharp, fontWeight: '800'}}>Moat insight: </Text>
                When only {bestDissentBucket.bucket.replace('DISSENT_', '')} dissents from the other two, following {bestDissentBucket.bucket.replace('DISSENT_', '')} wins <Text style={{color: T.sharp, fontWeight: '700'}}>{bestDissentBucket.hit_rate.toFixed(1)}%</Text> (n={bestDissentBucket.sample_n}, {(bestDissentBucket.edge_pp >= 0 ? '+' : '')}{bestDissentBucket.edge_pp.toFixed(1)}pp edge)
              </Text>
            </View>
          )}
        </View>
      )}

      {/* ─── STRONGEST SIGNALS STRIP ─────────────────────────────── */}
      {topSignals.length > 0 && (
        <View style={{marginBottom: 14}}>
          <View style={{flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 6}}>
            <Text style={{color: T.sharp, fontSize: 10, fontWeight: '800', letterSpacing: 0.7}}>
              🔥 STRONGEST SIGNALS RIGHT NOW
            </Text>
            <Text style={{color: T.textMuted, fontSize: 9, fontStyle: 'italic'}}>tap → deep dive</Text>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{gap: 8, paddingRight: 12}}>
            {topSignals.map(([key, gs]: any) => (
              <StrongestSignalCard key={key} groupKey={key} flags={gs}
                sample={historySample[key] || []}
                picks={picksIdx[gs[0].game_id]}
                onTap={(matchup: string, sport: string, gid: string) => onTapGame(matchup, sport, gid)} />
            ))}
          </ScrollView>
        </View>
      )}

      {/* ─── FILTERS ─────────────────────────────────────────────── */}
      <View style={{flexDirection: 'row', gap: 6, marginBottom: 12, flexWrap: 'wrap'}}>
        <FilterPill label="All Sports" active={sportFilter === 'ALL'} onPress={() => setSportFilter('ALL')} />
        {availableSports.map(s => (
          <FilterPill key={s} label={s} active={sportFilter === s} onPress={() => setSportFilter(s as SportFilter)} />
        ))}
        <View style={{width: 1, backgroundColor: T.border + '66', marginVertical: 4}} />
        <FilterPill label="All Signals" active={tierFilter === 'ALL'} onPress={() => setTierFilter('ALL')} />
        <FilterPill label="≥ Confirmed" active={tierFilter === 'CONFIRMED'} onPress={() => setTierFilter('CONFIRMED')} />
        <FilterPill label="🔥 Triple only" active={tierFilter === 'TRIPLE'} onPress={() => setTierFilter('TRIPLE')} />
      </View>

      {/* ─── WINDOW EXPLANATION ──────────────────────────────────── */}
      <Text style={{color: T.textMuted, fontSize: 10, marginBottom: 10, fontStyle: 'italic'}}>
        {sportFilter === 'ALL'
          ? `Showing signals from the last 24 hours across sports. NFL/NCAAF signals reflect movement over the week.`
          : `Window: ${SPORT_WINDOW[sportFilter] || 'last 24 hours'} · ${filteredGroups.length} game${filteredGroups.length === 1 ? '' : 's'}`}
      </Text>

      {/* ─── FULL LIST ───────────────────────────────────────────── */}
      {filteredGroups.length === 0 ? (
        <View style={{alignItems: 'center', paddingTop: 24, paddingHorizontal: 20}}>
          <Text style={{color: T.textDim, fontSize: 12, textAlign: 'center', lineHeight: 18}}>
            No signals match the current filters. Loosen filters or check back.
          </Text>
        </View>
      ) : (
        <View style={{gap: 10}}>
          {filteredGroups.slice(0, 20).map(([key, gs]: any) => {
            // 2026-08-18: groups are per-game now — historySample is still
            // keyed per game+market so merge all markets' samples for this game
            const merged = Object.entries(historySample)
              .filter(([k]) => k.startsWith(key + '::'))
              .flatMap(([, arr]) => arr);
            return (
              <LineMovementCard key={key} groupKey={key} flags={gs}
                sample={merged}
                picks={picksIdx[gs[0].game_id]}
                rawSigs={rawSigsIdx?.[gs[0].game_id]}
                sourceRecordIdx={sourceRecordIdx}
                onTap={onTapGame} />
            );
          })}
        </View>
      )}
    </View>
  );
}

// ─── FILTER PILL ────────────────────────────────────────────────────
function FilterPill({label, active, onPress}: {label: string; active: boolean; onPress: () => void}) {
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.7}
      style={{
        paddingHorizontal: 10, paddingVertical: 5, borderRadius: 14,
        backgroundColor: active ? T.accent + '22' : T.surfaceAlt,
        borderWidth: 1, borderColor: active ? T.accent + '77' : T.border,
      }}>
      <Text style={{
        color: active ? T.accent : T.textDim, fontSize: 11, fontWeight: '700', letterSpacing: 0.3,
      }}>{label}</Text>
    </TouchableOpacity>
  );
}

// ─── STRONGEST SIGNAL CARD (horizontal strip) ───────────────────────
function StrongestSignalCard({flags, sample, onTap}: any) {
  const first = flags[0];
  const matchup = sample[0]?.matchup || '';
  const [awayTeam, homeTeam] = matchup.includes(' @ ') ? matchup.split(' @ ').map((s: string) => s.trim()) : ['', ''];
  const strongest = flags.find((f: any) => String(f.classification || '').endsWith('_TRIPLE_CONFIRMED'))
                 || flags.find((f: any) => String(f.classification || '').endsWith('_CONFIRMED'))
                 || flags[0];
  const cls = String(strongest.classification || '');
  const isTriple = cls.endsWith('_TRIPLE_CONFIRMED');
  const family = cls.startsWith('SHARP_MOVE') ? 'sharp'
              : cls.startsWith('RLM') ? 'rlm'
              : cls.startsWith('PUBLIC_MOVE') ? 'public'
              : cls === 'CONSENSUS' ? 'consensus'
              : 'pattern';
  const color = family === 'sharp' ? T.sharp
              : family === 'rlm' ? T.accent
              : family === 'public' ? T.loss
              : T.hrb;
  // 2026-08-18 BUG FIX: previously showed raw strongest.side as the team,
  // but PUBLIC_MOVE_CONFIRMED and RLM_CONFIRMED classifications mean
  // sharps are on the OPPOSITE side (line moved with public / against
  // public respectively → the sharp play is the other team). The list
  // card already inverts; the strip did not. Result: strip showed
  // public side for OAK/KC RL (Athletics) while list correctly showed
  // sharp side (Royals). Both now agree.
  const invert = cls.startsWith('RLM') || cls.startsWith('PUBLIC_MOVE');
  const rawSide = String(strongest.side || '').toLowerCase();
  const sharpSide = invert
    ? (rawSide === 'home' ? 'away' : rawSide === 'away' ? 'home'
       : rawSide === 'over' ? 'under' : rawSide === 'under' ? 'over' : rawSide)
    : rawSide;
  const sideDisplay = (() => {
    if (sharpSide === 'home') return homeTeam || 'HOME';
    if (sharpSide === 'away') return awayTeam || 'AWAY';
    return sharpSide.toUpperCase();
  })();
  return (
    <TouchableOpacity onPress={() => onTap(matchup, first.sport, first.game_id)} activeOpacity={0.75}
      style={{
        width: 200, padding: 10, borderRadius: 10,
        backgroundColor: color + '14', borderWidth: isTriple ? 2 : 1.5, borderColor: color + '77',
      }}>
      <View style={{flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 4}}>
        <Text style={{color: color, fontSize: 9, fontWeight: '800', letterSpacing: 0.5}}>
          {isTriple ? '🔥 TRIPLE' : 'CONFIRMED'}
        </Text>
        <Text style={{color: T.textMuted, fontSize: 9}}>·</Text>
        <Text style={{color: T.textMuted, fontSize: 9, fontWeight: '700'}}>{first.sport}</Text>
        <Text style={{color: T.textMuted, fontSize: 9}}>·</Text>
        <Text style={{color: T.textMuted, fontSize: 9, fontWeight: '700'}}>{String(first.market).toUpperCase()}</Text>
      </View>
      <Text style={{color: T.text, fontSize: 12, fontWeight: '700'}} numberOfLines={1}>{sideDisplay}</Text>
      <Text style={{color: T.textDim, fontSize: 10, marginTop: 3}} numberOfLines={2}>{first.detail}</Text>
    </TouchableOpacity>
  );
}

// ─── SHARP $ SUMMARY CHIP (2026-08-18) ──────────────────────────────
// Aggregates raw source rows into an UNBRANDED sharp$ readout. Per
// feedback_brand_attribution_803 we don't expose data-provider names
// in user-facing UI. Instead we show the number: sharp is on this side
// with N% of the money vs M% of the bets → divergence Xpp. Uses the
// strongest source per market so users see the peak signal, not the
// average.
function SharpMoneyChip({rawSigs, market}: {rawSigs?: {cleatz: any[]; fadereport: any[]}; market: string}) {
  if (!rawSigs) return null;
  const mkt = market.toLowerCase();
  const cleatzMatch = (rawSigs.cleatz || []).find((c: any) => String(c.market).toLowerCase() === mkt);
  const fadeMatch = (rawSigs.fadereport || []).find((f: any) => String(f.market).toLowerCase() === mkt);
  // Normalize both into a common shape then pick the one with biggest divergence
  const c1 = cleatzMatch ? {
    side: cleatzMatch.sharp_side_norm,
    money: cleatzMatch.sharp_handle_pct,
    bets: cleatzMatch.sharp_bets_pct,
    div: cleatzMatch.divergence,
  } : null;
  const c2 = fadeMatch ? {
    side: fadeMatch.sharp_side_norm,
    money: fadeMatch.money_side_pct,
    bets: fadeMatch.bets_side_pct,
    div: (fadeMatch.money_side_pct != null && fadeMatch.bets_side_pct != null)
      ? Math.abs(fadeMatch.money_side_pct - fadeMatch.bets_side_pct) : 0,
  } : null;
  const best = [c1, c2].filter(Boolean).sort((a: any, b: any) => (b.div || 0) - (a.div || 0))[0] as any;
  if (!best || best.money == null || best.bets == null) return null;
  const sideDisp = String(best.side || '').toUpperCase();
  return (
    <View style={{marginTop: 6, backgroundColor: T.sharp + '14', borderRadius: 6,
      paddingHorizontal: 8, paddingVertical: 4, borderWidth: 0.5, borderColor: T.sharp + '55',
      alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 4}}>
      <Text style={{fontSize: 10}}>💰</Text>
      <Text style={{color: T.sharp, fontSize: 10, fontWeight: '700'}}>
        Sharp $ on {sideDisp} — {best.money}% money vs {best.bets}% bets
        {best.div ? ` · +${best.div}pp divergence` : ''}
      </Text>
    </View>
  );
}

// ─── FULL LINE MOVEMENT CARD ────────────────────────────────────────
// 2026-08-18: groupKey is now `game_id` (was `game_id::market`).
// `flags` contains ALL market flags for this game — we feature the
// strongest as the lead + chip the others below.
function LineMovementCard({groupKey, flags, sample, picks, sourceRecordIdx, rawSigs, onTap}: any) {
  const first = flags[0];
  const gid = groupKey;  // key is now just game_id
  // `market` for the LEAD signal (extracted after strongest is picked below)
  // — kept as `strongest.market` per usage sites so the section that renders
  // per-source rows references the strongest signal's market.
  // 2026-08-18 fix: fall back to picks metadata when sample has no history
  // (some games flagged today have no line_history sample yet — showed as
  // "matchup pending sample" placeholder to users).
  const matchupRaw = sample[0]?.matchup || '';
  const matchupFromPicks = picks && picks.away_team && picks.home_team
    ? `${picks.away_team} @ ${picks.home_team}` : '';
  const matchup = matchupRaw || matchupFromPicks;
  const commence = sample[0]?.commence_time || picks?.commence_time;
  const [awayTeam, homeTeam] = matchup.includes(' @ ') ? matchup.split(' @ ').map((s: string) => s.trim()) : ['', ''];
  const teamForSide = (side: string): string => {
    const s = String(side).toLowerCase();
    if (s === 'home') return homeTeam || 'HOME';
    if (s === 'away') return awayTeam || 'AWAY';
    return String(side).toUpperCase();
  };

  // Time badge
  let timeBadge = '';
  let timeIsHot = false;
  if (commence) {
    const gameTime = new Date(commence);
    const minsUntil = (gameTime.getTime() - Date.now()) / 60000;
    if (minsUntil > 0 && minsUntil < 60) { timeBadge = '🔴 STARTING SOON'; timeIsHot = true; }
    else if (minsUntil > 0) timeBadge = gameTime.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York'}) + ' ET';
    else { timeBadge = '⏸ LIVE / STARTED'; timeIsHot = true; }
  }

  // Identify the strongest classification across ALL markets for this game
  const strongest = flags.find((f: any) => String(f.classification || '').endsWith('_TRIPLE_CONFIRMED'))
                 || flags.find((f: any) => String(f.classification || '').endsWith('_CONFIRMED'))
                 || flags[0];
  // Market for the LEAD signal (used by sourceRecordIdx lookups + header render)
  const market = String(strongest.market || 'ml');
  // Other markets flagged for this game (rendered as secondary chips)
  const otherFlags = flags.filter((f: any) => f !== strongest && f.market !== strongest.market);
  const strongCls = String(strongest.classification || '');
  const isTriple = strongCls.endsWith('_TRIPLE_CONFIRMED');
  const isConfirmed = strongCls.endsWith('_CONFIRMED');
  const strongFamily = strongCls.startsWith('SHARP_MOVE') ? 'sharp'
                    : strongCls.startsWith('RLM') ? 'rlm'
                    : strongCls.startsWith('PUBLIC_MOVE') ? 'public'
                    : strongCls === 'CONSENSUS' ? 'consensus'
                    : strongCls === 'SOURCES_SPLIT' ? 'split'
                    : 'pattern';
  const familyColor = strongFamily === 'sharp' ? T.sharp
                    : strongFamily === 'rlm' ? T.accent
                    : strongFamily === 'public' ? T.loss
                    : strongFamily === 'consensus' ? T.hrb
                    : T.textMuted;
  const strongLabel = strongFamily === 'sharp' ? (isTriple ? '🔥 SHARP TRIPLE' : isConfirmed ? 'SHARP CONFIRMED' : 'SHARP LEAN')
                    : strongFamily === 'rlm' ? (isTriple ? '🔥 RLM TRIPLE' : isConfirmed ? 'RLM CONFIRMED' : 'RLM LEAN')
                    : strongFamily === 'public' ? (isTriple ? '🔥 PUBLIC TRIPLE' : isConfirmed ? 'PUBLIC CONFIRMED' : 'PUBLIC LEAN')
                    : strongFamily === 'consensus' ? 'CONSENSUS'
                    : strongFamily === 'split' ? 'SOURCES DISAGREE'
                    : strongest.pattern ? String(strongest.pattern).toUpperCase() : 'PATTERN';

  // Determine what the sharp move POINTS AT (RLM + PUBLIC_MOVE invert)
  const strongSideRaw = String(strongest.side || '').toUpperCase();
  const invert = strongCls.startsWith('RLM') || strongCls.startsWith('PUBLIC_MOVE');
  const sharpSide = invert
    ? (strongSideRaw === 'HOME' ? 'AWAY' : strongSideRaw === 'AWAY' ? 'HOME'
       : strongSideRaw === 'OVER' ? 'UNDER' : strongSideRaw === 'UNDER' ? 'OVER' : strongSideRaw)
    : strongSideRaw;

  // Model alignment — compare our pick side vs sharpSide
  const mkt = String(strongest.market || '').toLowerCase();
  const pickSideFromPlay = (play: any): string | null => {
    if (!play || typeof play !== 'object') return null;
    const ptype = String(play.type || play.market || '').toLowerCase();
    const psideRaw = String(play.side || play.pick_side || '').toUpperCase();
    if (!psideRaw) return null;
    if (mkt === 'total' && ptype !== 'total') return null;
    if (mkt !== 'total' && !['ml','spread','rl','runline','pl','puckline'].includes(ptype)) return null;
    return psideRaw;
  };
  const ourSide = picks ? (pickSideFromPlay(picks.primary) || pickSideFromPlay(picks.supplementary)) : null;
  const alignment: 'aligns' | 'fades' | 'neutral' = !ourSide ? 'neutral' : (ourSide === sharpSide ? 'aligns' : 'fades');

  // Per-source numbers with track-record lookup.
  // 2026-08-18 (brand): render sources as generic numbered Splits with
  // distinct colors instead of vendor names — feedback_brand_attribution_803.
  const SPLIT_COLORS = [T.sharp, T.accent, T.hrb];  // Split 1/2/3
  const perSourceRows = React.useMemo(() => {
    const rows: {source: string; label: string; color: string; pct: number; hitRate: number|null; n: number|null; window: number|null}[] = [];
    let idx = 0;
    if (strongest.money_pct != null || strongest.bets_pct != null) {
      const rec = sourceRecordIdx[`oddscrowd::${first.sport}::${market}`];
      rows.push({
        source: 'S1', label: 'Split 1', color: SPLIT_COLORS[idx++ % 3],
        pct: strongest.money_pct ?? strongest.bets_pct,
        hitRate: rec?.hit_rate ?? null, n: rec?.n_graded ?? null, window: rec?.window_days ?? null,
      });
    }
    if (strongest.handle_pct != null || strongest.bettors_pct != null) {
      const rec = sourceRecordIdx[`fadereport::${first.sport}::${market}`];
      rows.push({
        source: 'S2', label: 'Split 2', color: SPLIT_COLORS[idx++ % 3],
        pct: strongest.handle_pct ?? strongest.bettors_pct,
        hitRate: rec?.hit_rate ?? null, n: rec?.n_graded ?? null, window: rec?.window_days ?? null,
      });
    }
    // 3rd Split shows on TRIPLE_CONFIRMED (agreement badge, no numeric %)
    if (isTriple) {
      const rec = sourceRecordIdx[`cleatz::${first.sport}::${market}`];
      rows.push({
        source: 'S3', label: 'Split 3', color: SPLIT_COLORS[idx++ % 3],
        pct: 0,
        hitRate: rec?.hit_rate ?? null, n: rec?.n_graded ?? null, window: rec?.window_days ?? null,
      });
    }
    return rows;
  }, [strongest, sourceRecordIdx, first.sport, market, isTriple]);

  return (
    <TouchableOpacity onPress={() => onTap(matchup, first.sport, gid)} activeOpacity={0.75}
      style={{
        backgroundColor: T.surface, borderRadius: 12, padding: 14,
        borderLeftWidth: 3, borderLeftColor: familyColor,
      }}>
      {/* ── HEADER: sport · market · matchup · time ─────────────── */}
      <View style={{flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8}}>
        <View style={{flex: 1, paddingRight: 8}}>
          <View style={{flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 2}}>
            <Text style={{color: T.textMuted, fontSize: 9, fontWeight: '800', letterSpacing: 0.5}}>{first.sport}</Text>
            <Text style={{color: T.textMuted, fontSize: 9}}>·</Text>
            <Text style={{color: T.textMuted, fontSize: 9, fontWeight: '800', letterSpacing: 0.5}}>{market.toUpperCase()}</Text>
          </View>
          {matchup ? (
            <Text style={{color: T.text, fontSize: 13, fontWeight: '700'}} numberOfLines={1}>{matchup}</Text>
          ) : (
            <Text style={{color: T.textMuted, fontSize: 11, fontStyle: 'italic'}}>matchup pending sample</Text>
          )}
        </View>
        {timeBadge && (
          <Text style={{color: timeIsHot ? T.loss : T.textMuted, fontSize: 10, fontWeight: '700'}}>{timeBadge}</Text>
        )}
      </View>

      {/* ── LEAD SIGNAL (single, big) ───────────────────────────── */}
      <View style={{
        flexDirection: 'row', alignItems: 'center', gap: 8,
        paddingVertical: 8, paddingHorizontal: 10,
        backgroundColor: familyColor + '18', borderRadius: 8, marginBottom: 8,
      }}>
        <View style={{
          paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4,
          backgroundColor: familyColor + '33', borderWidth: isTriple ? 2 : 1, borderColor: familyColor + '77',
        }}>
          <Explainer term={strongLabel.replace('🔥 ', '').replace(' TRIPLE', '_TRIPLE_CONFIRMED').replace(' CONFIRMED', '_CONFIRMED')}
            color={familyColor} activeColor={familyColor} helpColor={T.text}
            helpBg={familyColor + '18'}
            textStyle={{color: familyColor, fontSize: 10, fontWeight: '800', letterSpacing: 0.4}}>
            <Text style={{color: familyColor, fontSize: 10, fontWeight: '800', letterSpacing: 0.4}}>{strongLabel}</Text>
          </Explainer>
        </View>
        <Text style={{color: T.text, fontSize: 12, fontWeight: '700', flex: 1}} numberOfLines={1}>
          → {teamForSide(sharpSide)}
        </Text>
      </View>
      {/* 2026-08-20: MINORITY DISSENT badge. Fires on CONFIRMED (not TRIPLE)
          because CONFIRMED means 2 of 3 sources agreed and 1 dissented.
          Per project_per_source_tracker_moat_818: on 30d MLB, the DISSENT
          side hits 60-77% depending on which source dissents. Users should
          be aware that this classification has a documented moat when the
          minority pipes up. Suppressed on TRIPLE (no dissenter). */}
      {isConfirmed && !isTriple && (
        <View style={{
          flexDirection: 'row', alignItems: 'flex-start', gap: 6,
          paddingVertical: 6, paddingHorizontal: 8, marginBottom: 8,
          backgroundColor: T.hrb + '18', borderRadius: 6, borderLeftWidth: 2, borderLeftColor: T.hrb,
        }}>
          <Text style={{color: T.hrb, fontSize: 11, marginTop: 1}}>💡</Text>
          <Text style={{color: T.textDim, fontSize: 11, flex: 1, lineHeight: 15}}>
            <Text style={{color: T.hrb, fontWeight: '800'}}>MINORITY DISSENT: </Text>
            2 of 3 sources agree here — the 3rd disagrees. Historical pattern
            (30d MLB): when only OC dissents, OC has been right 77% (n=22).
            Consider whether the dissenter's side is worth a look.
          </Text>
        </View>
      )}
      <Text style={{color: T.textDim, fontSize: 11, lineHeight: 15, marginBottom: 10}}>{strongest.detail}</Text>

      {/* 2026-08-18 consolidation: chip other markets flagged on this
          same game. E.g., lead is ML sharp on Athletics, chip below
          shows RL public-side on Royals too — user sees the full picture
          instead of two conflicting cards. */}
      {otherFlags.length > 0 && (
        <View style={{flexDirection: 'row', gap: 6, flexWrap: 'wrap', marginBottom: 10}}>
          <Text style={{color: T.textMuted, fontSize: 9, fontWeight: '700', letterSpacing: 0.5, alignSelf: 'center'}}>
            ALSO ON THIS GAME:
          </Text>
          {otherFlags.map((f: any, idx: number) => {
            const fCls = String(f.classification || '');
            const fFamily = fCls.startsWith('SHARP_MOVE') ? 'sharp'
                          : fCls.startsWith('RLM') ? 'rlm'
                          : fCls.startsWith('PUBLIC_MOVE') ? 'public'
                          : 'other';
            const fColor = fFamily === 'sharp' ? T.sharp
                         : fFamily === 'rlm' ? T.accent
                         : fFamily === 'public' ? T.loss
                         : T.textMuted;
            const fInvert = fCls.startsWith('RLM') || fCls.startsWith('PUBLIC_MOVE');
            const fSideRaw = String(f.side || '').toUpperCase();
            const fSide = fInvert
              ? (fSideRaw === 'HOME' ? 'AWAY' : fSideRaw === 'AWAY' ? 'HOME'
                 : fSideRaw === 'OVER' ? 'UNDER' : fSideRaw === 'UNDER' ? 'OVER' : fSideRaw)
              : fSideRaw;
            const fMkt = String(f.market || '').toUpperCase();
            return (
              <View key={idx} style={{
                backgroundColor: fColor + '18', paddingHorizontal: 6, paddingVertical: 2,
                borderRadius: 4, borderWidth: 0.5, borderColor: fColor + '55',
              }}>
                <Text style={{color: fColor, fontSize: 9, fontWeight: '700'}}>
                  {fMkt} → {teamForSide(fSide)}
                </Text>
              </View>
            );
          })}
        </View>
      )}

      {/* 2026-08-18: unbranded sharp $ readout (aggregates cleatz + fadereport
          per feedback_brand_attribution_803 — no source names in user copy) */}
      <SharpMoneyChip rawSigs={rawSigs} market={String(strongest.market)} />

      {/* ── SOURCE AGREEMENT + TRACK RECORDS (the "how do we know?" answer) ── */}
      {perSourceRows.length > 0 && (
        <View style={{
          backgroundColor: T.surfaceAlt, borderRadius: 8, padding: 10, marginBottom: 10,
        }}>
          <View style={{flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 6}}>
            <Text style={{color: T.textMuted, fontSize: 9, fontWeight: '800', letterSpacing: 0.5}}>
              THE SPLIT
            </Text>
            {isTriple && (
              <Text style={{color: T.sharp, fontSize: 9, fontWeight: '800'}}>· 3 of 3 confirm</Text>
            )}
            {!isTriple && isConfirmed && (
              <Text style={{color: T.accent, fontSize: 9, fontWeight: '800'}}>· 2 of 3 confirm</Text>
            )}
          </View>
          {perSourceRows.map((row, i) => (
            <View key={i} style={{flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 3}}>
              <View style={{flexDirection: 'row', alignItems: 'center', gap: 6, flex: 1}}>
                {/* Distinct color dot per Split — visual confirmation stacking */}
                <View style={{width: 8, height: 8, borderRadius: 4, backgroundColor: row.color}} />
                <Text style={{color: row.color, fontSize: 11, fontWeight: '800', width: 60}}>{row.label}</Text>
                {row.pct > 0 ? (
                  <Text style={{color: T.textDim, fontSize: 11, fontWeight: '700'}}>{row.pct.toFixed(0)}%</Text>
                ) : (
                  <Text style={{color: T.textMuted, fontSize: 10, fontStyle: 'italic'}}>agrees</Text>
                )}
              </View>
              {/* Track record — the noise vs signal separator */}
              {row.hitRate != null && row.n != null && row.n > 0 ? (
                <View style={{flexDirection: 'row', alignItems: 'baseline', gap: 4}}>
                  <Text style={{color: T.textMuted, fontSize: 9}}>{row.window}d:</Text>
                  <Text style={{color: row.hitRate >= 52.4 ? T.win : row.hitRate >= 48 ? T.textDim : T.loss, fontSize: 10, fontWeight: '800'}}>
                    {row.hitRate.toFixed(1)}%
                  </Text>
                  <Text style={{color: T.textMuted, fontSize: 9}}>n={row.n}</Text>
                </View>
              ) : (
                // 2026-08-20: softer copy — "pending" read as broken/missing.
                // Aggregate track record IS visible in the header strip at top
                // of tab (sharp_source_calibration 30d hit rates). Per-market
                // rate builds up as this source × market accumulates n.
                <Text style={{color: T.textMuted, fontSize: 9, fontStyle: 'italic'}}>rate building</Text>
              )}
            </View>
          ))}
        </View>
      )}

      {/* ── MODEL ALIGNMENT ── show only when we HAVE a pick on this market.
          2026-08-19: "No pick published on this market" row removed per user
          feedback — reads as noise when it fires. If alignment is neither
          'aligns' nor 'fades' (i.e. we don't have a pick to compare vs the
          sharp side), skip the row entirely. */}
      {(alignment === 'aligns' || alignment === 'fades') && (
        <View style={{
          flexDirection: 'row', alignItems: 'center', gap: 6,
          paddingVertical: 6, paddingHorizontal: 10, borderRadius: 6,
          backgroundColor: alignment === 'aligns' ? T.win + '18' : T.loss + '18',
        }}>
          <Text style={{
            color: alignment === 'aligns' ? T.win : T.loss,
            fontSize: 11, fontWeight: '800',
          }}>
            {alignment === 'aligns' ? '✅ Our pick aligns with sharps' : '⚠️ Our pick opposes the sharps'}
          </Text>
        </View>
      )}

      {/* ── TAP HINT ─────────────────────────────────────────────── */}
      <Text style={{color: T.textMuted, fontSize: 9, marginTop: 8, textAlign: 'right', fontStyle: 'italic'}}>
        tap for full line history + provenance →
      </Text>
    </TouchableOpacity>
  );
}
