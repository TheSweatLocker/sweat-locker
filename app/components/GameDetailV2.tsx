/**
 * GameDetailV2 — sport-agnostic game detail body approved 2026-07-29
 * ([[project_game_detail_redesign_729]]).
 *
 * Replaces the ~650-line inline modal block at app/index.tsx 12675-13328.
 * Kills the cross-book SVG chart, standalone NRFI section, "Log a Pick"
 * chips, and verbose Sweat Score card. Adds Money Flow (differentiator),
 * Alignment status strip, Predicted Score RANGE (not blind avg), Stat
 * Projections section, and a collapsed Numbers panel for depth users.
 *
 * Data sources (all lookup from ctx which is the sport's game_context row):
 *   oddscrowd_snapshot  → Money Flow bars
 *   align_status        → Alignment status strip + verdict chip
 *   panel_implied_*     → Model lens grid + Stat Projections
 *   jerry_pred_*        → Model lens grid + Predicted Score range
 *   projected_*         → Model lens grid (v3)
 *   model_pred_*        → Model lens grid (v4)
 *   mc_probabilities    → Model lens (MC) + Numbers panel
 *   signal_confluence_* → Cohorts panel + Numbers panel
 *   primary_play        → Hero verdict card
 *   *_pitcher_projected_* → Stat Projections (MLB slot)
 *
 * Sport-specific slots live between Handicappers and Cohorts. Only MLB slot
 * (PitcherMatchupSlot) is filled out in this first pass; NFL/NBA/UFC/NHL
 * render lightweight placeholders until their sport-specific data is wired.
 */
import React, {useState, useMemo, useEffect} from 'react';
import {View, Text, TouchableOpacity, ScrollView, StyleSheet, Platform} from 'react-native';
import {createClient} from '@supabase/supabase-js';
import Explainer from './Explainer';
import { personaFor, scrubSourceNames } from '../lib/sourcePersona';
import { abbrev as teamAbbrev } from '../lib/teamAbbrev';

// Standard sport-league abbreviations — Dodgers → LAD (not DOD)
const TEAM_ABBREV: Record<string, string> = {
  // MLB
  'Arizona Diamondbacks': 'ARI', 'Atlanta Braves': 'ATL', 'Baltimore Orioles': 'BAL',
  'Boston Red Sox': 'BOS', 'Chicago Cubs': 'CHC', 'Chicago White Sox': 'CWS',
  'Cincinnati Reds': 'CIN', 'Cleveland Guardians': 'CLE', 'Colorado Rockies': 'COL',
  'Detroit Tigers': 'DET', 'Houston Astros': 'HOU', 'Kansas City Royals': 'KC',
  'Los Angeles Angels': 'LAA', 'Los Angeles Dodgers': 'LAD', 'Miami Marlins': 'MIA',
  'Milwaukee Brewers': 'MIL', 'Minnesota Twins': 'MIN', 'New York Mets': 'NYM',
  'New York Yankees': 'NYY', 'Oakland Athletics': 'OAK', 'Athletics': 'ATH',
  'Philadelphia Phillies': 'PHI', 'Pittsburgh Pirates': 'PIT', 'San Diego Padres': 'SD',
  'San Francisco Giants': 'SF', 'Seattle Mariners': 'SEA', 'St. Louis Cardinals': 'STL',
  'Tampa Bay Rays': 'TB', 'Texas Rangers': 'TEX', 'Toronto Blue Jays': 'TOR',
  'Washington Nationals': 'WSH',
  // NFL — standard 2-3 letter
  'Arizona Cardinals': 'ARI', 'Atlanta Falcons': 'ATL', 'Baltimore Ravens': 'BAL',
  'Buffalo Bills': 'BUF', 'Carolina Panthers': 'CAR', 'Chicago Bears': 'CHI',
  'Cincinnati Bengals': 'CIN', 'Cleveland Browns': 'CLE', 'Dallas Cowboys': 'DAL',
  'Denver Broncos': 'DEN', 'Detroit Lions': 'DET', 'Green Bay Packers': 'GB',
  'Houston Texans': 'HOU', 'Indianapolis Colts': 'IND', 'Jacksonville Jaguars': 'JAX',
  'Kansas City Chiefs': 'KC', 'Las Vegas Raiders': 'LV', 'Los Angeles Chargers': 'LAC',
  'Los Angeles Rams': 'LAR', 'Miami Dolphins': 'MIA', 'Minnesota Vikings': 'MIN',
  'New England Patriots': 'NE', 'New Orleans Saints': 'NO', 'New York Giants': 'NYG',
  'New York Jets': 'NYJ', 'Philadelphia Eagles': 'PHI', 'Pittsburgh Steelers': 'PIT',
  'San Francisco 49ers': 'SF', 'Seattle Seahawks': 'SEA', 'Tampa Bay Buccaneers': 'TB',
  'Tennessee Titans': 'TEN', 'Washington Commanders': 'WAS',
  // NBA
  'Atlanta Hawks': 'ATL', 'Boston Celtics': 'BOS', 'Brooklyn Nets': 'BKN',
  'Charlotte Hornets': 'CHA', 'Chicago Bulls': 'CHI', 'Cleveland Cavaliers': 'CLE',
  'Dallas Mavericks': 'DAL', 'Denver Nuggets': 'DEN', 'Detroit Pistons': 'DET',
  'Golden State Warriors': 'GSW', 'Houston Rockets': 'HOU', 'Indiana Pacers': 'IND',
  'LA Clippers': 'LAC', 'Los Angeles Clippers': 'LAC', 'Los Angeles Lakers': 'LAL',
  'Memphis Grizzlies': 'MEM', 'Miami Heat': 'MIA', 'Milwaukee Bucks': 'MIL',
  'Minnesota Timberwolves': 'MIN', 'New Orleans Pelicans': 'NOP', 'New York Knicks': 'NYK',
  'Oklahoma City Thunder': 'OKC', 'Orlando Magic': 'ORL', 'Philadelphia 76ers': 'PHI',
  'Phoenix Suns': 'PHX', 'Portland Trail Blazers': 'POR', 'Sacramento Kings': 'SAC',
  'San Antonio Spurs': 'SA', 'Toronto Raptors': 'TOR', 'Utah Jazz': 'UTA',
  'Washington Wizards': 'WAS',
  // NHL
  'Anaheim Ducks': 'ANA', 'Arizona Coyotes': 'ARI', 'Boston Bruins': 'BOS',
  'Buffalo Sabres': 'BUF', 'Calgary Flames': 'CGY', 'Carolina Hurricanes': 'CAR',
  'Chicago Blackhawks': 'CHI', 'Colorado Avalanche': 'COL', 'Columbus Blue Jackets': 'CBJ',
  'Dallas Stars': 'DAL', 'Detroit Red Wings': 'DET', 'Edmonton Oilers': 'EDM',
  'Florida Panthers': 'FLA', 'Los Angeles Kings': 'LAK', 'Minnesota Wild': 'MIN',
  'Montreal Canadiens': 'MTL', 'Nashville Predators': 'NSH', 'New Jersey Devils': 'NJD',
  'New York Islanders': 'NYI', 'New York Rangers': 'NYR', 'Ottawa Senators': 'OTT',
  'Philadelphia Flyers': 'PHI', 'Pittsburgh Penguins': 'PIT', 'San Jose Sharks': 'SJS',
  'Seattle Kraken': 'SEA', 'St. Louis Blues': 'STL', 'Tampa Bay Lightning': 'TBL',
  'Toronto Maple Leafs': 'TOR', 'Vancouver Canucks': 'VAN', 'Vegas Golden Knights': 'VGK',
  'Washington Capitals': 'WSH', 'Winnipeg Jets': 'WPG',
};

// Lazy Supabase client (reads EXPO_PUBLIC_ env at first use)
let _sb: any = null;
function sb() {
  if (_sb) return _sb;
  const url = process.env.EXPO_PUBLIC_SUPABASE_URL;
  const key = process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  _sb = createClient(url, key);
  return _sb;
}

// ─── Palette (matches mock in artifact_URL) ─────────────────────────────
const C = {
  bg: '#0e1116',
  surface: '#161b23',
  surface2: '#1c232d',
  surface3: '#232c39',
  border: '#2a3341',
  borderStrong: '#3b4656',
  text: '#e6ebef',
  textMuted: '#7a8894',
  // 2026-09-01: bumped textDim from '#556270' (contrast 3.3:1 on
  // C.bg — borderline unreadable, user reported as "black text" in
  // Team Tendencies + Recent Schedule + Team Stats cards) to '#8898a5'
  // (contrast 7.5:1, WCAG AAA). Every card that uses textDim for
  // "non-advantaged team stat" or muted labels benefits — TeamTendencies,
  // NCAAFTeamMatchup, NBAFourFactors, NCAABEfficiency, LensGrid, all
  // three new Tier 1 cards (Recent Schedule, Situational, Team Stats).
  textDim: '#8898a5',
  accent: '#00c785',
  accentDim: 'rgba(0,199,133,0.14)',
  accentBg: 'rgba(0,199,133,0.10)',
  sharp: '#5aa9ff',
  sharpDim: 'rgba(90,169,255,0.14)',
  warn: '#f0b34a',
  warnDim: 'rgba(240,179,74,0.12)',
  fade: '#e05561',
  fadeDim: 'rgba(224,85,97,0.12)',
  home: '#e8b8ff',
  away: '#a8d8ff',
  overlay: 'rgba(255,255,255,0.05)',
};

// ─── Types ──────────────────────────────────────────────────────────────
type SportCode = 'MLB'|'NFL'|'NCAAF'|'NBA'|'NCAAB'|'UFC'|'NHL';

type Props = {
  game: any;
  ctx: any;                 // sport's game_context row (nullable when not loaded)
  gamesSport: SportCode;
  externalPicks?: any[];    // rows from external_picks (non-oddscrowd) — if omitted, fetched inside
  gameProps?: any[];        // rows from mlb_pipeline_props / nfl_props etc. — if omitted, fetched inside
  historicalOdds?: any;     // {opening_spread, opening_total, opening_ml_home, opening_ml_away}
  jerryNarrative?: string;  // Jerry's LLM-generated read for this game (markdown).
                            // Prefers new jerry_reads.long_read, falls back to jerry_cache.
  jerrySynthesis?: {        // NEW (2026-07-31): parseable directional call from jerry_reads.
    call_text?: string;     // e.g. "Pittsburgh Pirates ML", "Under 8.5", "Pass"
    conviction?: number;    // 0-100
    call_market?: string;   // 'ml' | 'rl' | 'total' | 'prop' | 'pass'
    call_side?: string;     // 'HOME' | 'AWAY' | 'OVER' | 'UNDER'
    generated_at?: string;
  };
  jerryLoading?: boolean;
  onClose: () => void;
  onAddParlayLeg?: (leg: any) => void;
  onLogPick?: (pick: any) => void;   // opens the manual log-pick modal pre-filled
};

// ─── Small util helpers ─────────────────────────────────────────────────
const f = (v: any, digits = 2): string => {
  if (v === null || v === undefined) return '—';
  const n = typeof v === 'number' ? v : parseFloat(v);
  if (!isFinite(n)) return '—';
  return n.toFixed(digits);
};

// Format American odds — positive gets a `+` prefix ("+149" not "149")
const fmtOdds = (v: any): string => {
  if (v === null || v === undefined || v === '' || v === '—') return '—';
  const n = typeof v === 'number' ? v : parseFloat(String(v));
  if (!isFinite(n)) return String(v);
  return n > 0 ? `+${n}` : String(n);
};

const signSide = (m: any): 'H'|'A'|null => {
  if (m === null || m === undefined) return null;
  const n = typeof m === 'number' ? m : parseFloat(m);
  if (!isFinite(n) || n === 0) return null;
  return n > 0 ? 'H' : 'A';
};

const sideColor = (side: 'H'|'A'|null) => side === 'H' ? C.home : side === 'A' ? C.away : C.textDim;

const abbrev3 = (team: string) => {
  if (!team) return '?';
  // Prefer standard sport-league abbreviation (LAD, MIA, LAA...)
  const canonical = TEAM_ABBREV[team.trim()];
  if (canonical) return canonical;
  // Fallback for unknown/international teams — use last word first 3 chars
  return team.split(' ').slice(-1)[0].slice(0, 3).toUpperCase();
};

// ─── Main component ─────────────────────────────────────────────────────
export default function GameDetailV2({
  game, ctx, gamesSport, externalPicks: externalPicksProp, gameProps: gamePropsProp,
  historicalOdds, jerryNarrative, jerrySynthesis, jerryLoading, onClose, onAddParlayLeg, onLogPick,
}: Props) {
  const [fetchedExternals, setFetchedExternals] = useState<any[]>([]);
  const [fetchedProps, setFetchedProps] = useState<any[]>([]);
  const [sourceRecords, setSourceRecords] = useState<Record<string, any>>({});

  // Auto-fetch externals + props per-game when parent doesn't supply.
  useEffect(() => {
    let cancelled = false;
    let client: any = null;
    try { client = sb(); } catch { /* client stays null */ }
    if (!client) return;

    (async () => {
      // Determine game_id + game_date
      let gid = ctx?.game_id;
      let gameDate = ctx?.game_date;
      const away = game?.away_team || ctx?.away_team;
      const home = game?.home_team || ctx?.home_team;

      // If we don't have gid but we have teams + date, look it up
      if (!gid && away && home) {
        // Try to derive game_date from game.commence_time if not on ctx
        if (!gameDate && game?.commence_time) {
          try {
            gameDate = new Date(game.commence_time).toLocaleDateString('en-CA', {timeZone: 'America/New_York'});
          } catch { /* skip */ }
        }
        if (gameDate) {
          const contextTable = gamesSport === 'MLB' ? 'mlb_game_context'
            : gamesSport === 'NFL' ? 'nfl_game_context'
            : gamesSport === 'NCAAF' ? 'ncaaf_game_context'
            : gamesSport === 'NCAAB' ? 'ncaab_game_context' : null;
          if (contextTable) {
            // 2026-08-23: Odds API returns team names WITH mascots ("TCU Horned
            // Frogs") while ctx tables store bare names ("TCU"). Exact .eq lookup
            // never matched for NCAAF/NCAAB/NFL. Use ilike with a "last word"
            // suffix match — matches "TCU" against "%TCU%" and "TCU Horned Frogs"
            // against "%Frogs%" if ctx happens to have the full name too.
            const awayShort = String(away).split(' ').filter(Boolean).slice(-1)[0] || away;
            const homeShort = String(home).split(' ').filter(Boolean).slice(-1)[0] || home;
            const {data: ctxData} = await client
              .from(contextTable)
              .select('game_id,game_date')
              .eq('game_date', gameDate)
              .ilike('home_team', `%${homeShort}%`)
              .ilike('away_team', `%${awayShort}%`)
              .limit(1);
            if (ctxData && ctxData.length) {
              gid = ctxData[0].game_id;
              gameDate = ctxData[0].game_date;
            }
          }
        }
      }

      if (!gid || !gameDate) {
        console.warn('[GameDetailV2] no game_id or game_date resolved — externals fetch skipped', {gid, gameDate, away, home});
        return;
      }

      // Fetch externals
      if (!externalPicksProp || externalPicksProp.length === 0) {
        const {data: extData, error: extErr} = await client
          .from('external_picks')
          .select('source,surface,pick_side,confidence,fade_flag,pick_line,odds_american')
          .eq('sport', gamesSport)
          .eq('game_date', gameDate)
          .eq('game_id', gid);
        if (extErr) console.warn('[GameDetailV2] externals fetch error:', extErr.message);
        if (!cancelled && extData) {
          console.log(`[GameDetailV2] fetched ${extData.length} external_picks for gid=${gid}`);
          setFetchedExternals(extData);
        }
        // 2026-08-26: also fetch 30d W-L record per source×surface so chips
        // can show "The Chalk 24-13" instead of just "The Chalk".
        const sources = Array.from(new Set((extData || []).map(e => e.source).filter(Boolean)));
        if (sources.length > 0) {
          const {data: trackData, error: trackErr} = await client
            .from('external_source_track_record')
            .select('source,surface,n_wins,n_losses,hit_rate')
            .eq('sport', gamesSport)
            .eq('window_days', 30)
            .in('source', sources);
          if (trackErr) console.warn('[GameDetailV2] track_record fetch error:', trackErr.message);
          if (!cancelled && trackData) {
            const map: Record<string, any> = {};
            for (const r of trackData) map[`${r.source}|${r.surface}`] = r;
            setSourceRecords(map);
          }
        }
      }

      // Fetch props (MLB only for now)
      if ((!gamePropsProp || gamePropsProp.length === 0) && gamesSport === 'MLB') {
        const {data: propData, error: propErr} = await client
          .from('mlb_pipeline_props')
          .select('player_name,player_team,prop_type,direction,prop_line,conviction,tier,signals')
          .eq('game_date', gameDate)
          .eq('game_id', gid)
          .order('conviction', {ascending: false})
          .limit(15);
        if (propErr) console.warn('[GameDetailV2] props fetch error:', propErr.message);
        if (!cancelled && propData) setFetchedProps(propData);
      }
    })();

    return () => { cancelled = true; };
  }, [game?.id, ctx?.game_id, ctx?.game_date, game?.away_team, game?.home_team, gamesSport, externalPicksProp, gamePropsProp]);

  // `??` falls back only on null/undefined — parent's `[]` would win over
  // fetched data. Prefer parent's data only if it's non-empty.
  const externalPicks = (externalPicksProp && externalPicksProp.length > 0)
    ? externalPicksProp : fetchedExternals;
  const gameProps = (gamePropsProp && gamePropsProp.length > 0)
    ? gamePropsProp : fetchedProps;

  if (!game) return null;

  // 2026-08-26: prefer ctx team name (our DB canonical) over game (Odds API).
  // Odds API includes mascot ('North Carolina Tar Heels', 'Texas Christian
  // Horned Frogs') which abbrev3's last-word fallback maps to 'HEE'/'FRO'
  // for NCAAF teams not in TEAM_ABBREV. Our ctx stores 'North Carolina' +
  // 'TCU' which map to 'NC'/'TCU' cleanly.
  const awayTeam = ctx?.away_team || game.away_team || 'Away';
  const homeTeam = ctx?.home_team || game.home_team || 'Home';
  const closeSpread = ctx?.close_spread ?? game.close_spread;
  const closeTotal = ctx?.close_total ?? game.close_total;
  // 2026-08-25: sports name ML columns differently on their context tables.
  //   MLB / NFL / NBA:  home_ml_close / away_ml_close
  //   NCAAF / NCAAB:    close_home_ml / close_away_ml
  // Read both so the Market card + LineMovement work everywhere.
  const homeML = ctx?.home_ml_close ?? ctx?.close_home_ml ?? game.home_ml;
  const awayML = ctx?.away_ml_close ?? ctx?.close_away_ml ?? game.away_ml;

  return (
    <View style={styles.root}>
      <StickyHeader
        away={awayTeam} home={homeTeam}
        time={game.commence_time_local || game.game_time || ''}
        venue={ctx?.venue || game.venue}
        onClose={onClose}
      />

      <ScrollView style={{flex: 1}} contentContainerStyle={{paddingBottom: 24}}>
        <VerdictCard ctx={ctx} awayTeam={awayTeam} homeTeam={homeTeam} />
        <LosingMarketChips ctx={ctx} />
        <JerryReadSection narrative={jerryNarrative} loading={jerryLoading} synthesis={jerrySynthesis} />
        <AlignmentStrip ctx={ctx} />

        <Section title="Market">
          <MarketRow
            closeSpread={closeSpread}
            closeTotal={closeTotal}
            homeML={homeML}
            awayML={awayML}
          />
        </Section>

        {/* 2026-09-01: gate on any predicted-score field. Was rendering
            empty "No score projections available" under the Section title
            on FCS games + sparse UFC / NHL cards. */}
        {hasAnyPredictedScore(ctx) && (
          <Section title="Predicted Score" hint="range across models">
            <ScoreRange ctx={ctx} awayTeam={awayTeam} homeTeam={homeTeam} />
          </Section>
        )}

        {gamesSport === 'MLB' && (
          <Section title="Stat Projections" hint="model-implied · check against your prop lines">
            <StatProjectionsMLB ctx={ctx} />
          </Section>
        )}

        <Section title="Money Flow" hint="bets vs money · sharps vs public">
          <MoneyFlow ctx={ctx} sport={gamesSport} />
        </Section>

        <Section title="Line Movement" hint="opening → current">
          <LineMovementStrip ctx={ctx} historicalOdds={historicalOdds} />
        </Section>

        {/* 2026-09-01: gate on any lens producing a value. Was showing
            empty "Model Consensus" header on thin UFC/NHL/FCS cards. */}
        {hasAnyLensValue(ctx, gamesSport) && (
          <Section title="Model Consensus" hint="margin (H+ / A−)">
            <LensGrid ctx={ctx} gamesSport={gamesSport} />
          </Section>
        )}

        {/* 2026-09-01: gate on non-OC pick presence. Was rendering
            empty "No handicapper picks pulled yet" on most NHL/UFC/
            some NCAAF cards. */}
        {(externalPicks || []).some((p: any) => p.source !== 'oddscrowd') && (
          <Section title="External Handicappers">
            <HandicappersRow picks={externalPicks} homeTeam={homeTeam} awayTeam={awayTeam} sport={gamesSport} records={sourceRecords} />
          </Section>
        )}

        <SportSpecificSlot ctx={ctx} gamesSport={gamesSport} game={game} />

        {/* 2026-09-01: Recent Schedule card — cross-sport, reads
            team_recent_games matview (populated by refresh_team_recent_games
            RPC called from each pipeline's resolver step). Three tabs:
            away / H2H / home. Silent hide when both teams have zero rows
            + no H2H (pre-season / matview not refreshed). See
            project_rolling_rollup_architecture_901 for the wider
            rollup-tables architecture. */}
        <Section title="Recent Schedule" hint="last 5 · ATS · O/U">
          <RecentScheduleCard sport={gamesSport} homeTeam={homeTeam} awayTeam={awayTeam} />
        </Section>

        {/* 2026-09-01: Situational Records — reads team_situational_records
            matview. Sub-tabs Spread/Total/ML × 4 filter rows (Overall,
            L10, Home/Away, Fav/Dog). Hit-% color coding (>=58 green,
            <=42 red). See project_rolling_rollup_architecture_901. */}
        <Section title="Situational Records" hint="records × market · hit-% color">
          <SituationalCard sport={gamesSport} homeTeam={homeTeam} awayTeam={awayTeam} season={ctx?.season} />
        </Section>

        {/* 2026-09-01: Team Stats — reads team_stats_rolling matview.
            Offense/Defense sub-tabs, each stat row shows raw value +
            rank chip (quintile-colored). NCAAF-only content today; MLB/
            NFL/NBA/NCAAB/NHL follow-up ships. See
            project_rolling_rollup_architecture_901. */}
        <Section title="Team Stats" hint="raw value + rank · ranks are FBS-only">
          <TeamStatsCard sport={gamesSport} homeTeam={homeTeam} awayTeam={awayTeam} season={ctx?.season} />
        </Section>

        {/* 2026-08-23: Public Splits panel — renders ctx.splits_summary
            (populated by splits_v2_pipeline aggregator). Shows sources_present
            + triple_confirmed markets. User feedback: college football game
            detail was missing splits despite backend data landing. */}
        {ctx?.splits_summary && (
          <Expander title="Public Splits" badge={splitsBadge(ctx.splits_summary)}>
            <SplitsSummaryPanel summary={ctx.splits_summary} />
          </Expander>
        )}

        {/* 2026-09-01: gate on breakdown presence — was rendering
            "COHORT SIGNALS · no data" on NHL/UFC/thin NCAAB cards. */}
        {safeJSON(ctx?.signal_confluence_breakdown) && (
          <Expander title="Cohort Signals" badge={cohortBadge(ctx)}>
            <CohortsPanel ctx={ctx} />
          </Expander>
        )}

        {/* 2026-09-01: gate Game Props expander to sports with actual
            prop data. Prior version rendered "Game Props · 0 signals"
            expander header on every NFL/NCAAF/etc. game (fetch is
            MLB-only at GameDetailV2.tsx:284-295), making cards look
            unfinished. Show only when we actually have props. */}
        {gameProps.length > 0 && (
          <Expander title="Game Props" badge={`${gameProps.length} signal${gameProps.length === 1 ? '' : 's'}`}>
            <GamePropsPanel props={gameProps} />
          </Expander>
        )}

        <Section title="Your Book · Hard Rock Bet" hint="tap to add parlay or log pick">
          <YourBookTiles
            closeSpread={closeSpread}
            closeTotal={closeTotal}
            homeML={homeML}
            awayML={awayML}
            homeTeam={homeTeam}
            awayTeam={awayTeam}
            primaryPlay={ctx?.primary_play}
            bookmakers={game.bookmakers || []}
            onAddParlayLeg={onAddParlayLeg}
            onLogPick={onLogPick}
          />
        </Section>

        {/* 2026-09-01: gate expander — was rendering "0 books" header
            on late-add NCAAF games where odds fetch missed. */}
        {(game.bookmakers || []).length > 0 && (
          <Expander title="All Book Lines" badge={`${(game.bookmakers || []).length} books`}>
            <AllBookLinesPanel
              bookmakers={game.bookmakers || []}
              homeTeam={homeTeam}
              awayTeam={awayTeam}
              onAddParlayLeg={onAddParlayLeg}
            />
          </Expander>
        )}

        <Expander title="📐 Numbers" badge="full model dump">
          <NumbersPanel ctx={ctx} awayTeam={awayTeam} homeTeam={homeTeam} sport={gamesSport} />
        </Expander>

        <View style={styles.footer}>
          <Text style={styles.footerText}>
            MORE DATA · LESS SWEAT · <Text style={{color: C.accent, fontWeight: '800'}}>THE SWEAT LOCKER</Text>
          </Text>
        </View>
      </ScrollView>
    </View>
  );
}

// ─── STICKY HEADER ──────────────────────────────────────────────────────
function StickyHeader({away, home, time, venue, onClose}: any) {
  return (
    <View style={styles.header}>
      <View style={{flex: 1, minWidth: 0}}>
        <Text style={styles.hdrMatchup} numberOfLines={2}>
          <Text style={{color: C.away}}>{away}</Text>
          <Text style={{color: C.textMuted}}>  @  </Text>
          <Text style={{color: C.home}}>{home}</Text>
        </Text>
        {(time || venue) && (
          <Text style={styles.hdrMeta} numberOfLines={1}>
            {[time, venue].filter(Boolean).join(' · ')}
          </Text>
        )}
      </View>
      <TouchableOpacity onPress={onClose} style={styles.closeBtn} activeOpacity={0.7}>
        <Text style={styles.closeBtnText}>✕</Text>
      </TouchableOpacity>
    </View>
  );
}

// ─── HERO VERDICT ───────────────────────────────────────────────────────
function VerdictCard({ctx, awayTeam, homeTeam}: any) {
  const play = ctx?.primary_play;
  if (!play || typeof play !== 'object') {
    return (
      <View style={styles.verdict}>
        <Text style={styles.verdictNoPlay}>No primary play surfaced for this game.</Text>
      </View>
    );
  }
  const label = play.label || '';
  const sub = play.sub || '';
  // 2026-08-20: tier badge REMOVED from game analysis surface per user
  // feedback. Tier chips (LEAN/STRONG/PRIME) only appear on curated pick
  // surfaces (Sharp Card, Sweat Card, Ladder, POTD, Dawg of Day, Daily
  // Degen). The game analysis card keeps the pick label + market type
  // + reasoning, but drops the confidence chip because forcing every
  // game into a tier label was making 90% of games render as "LEAN"
  // (honest data reality — most games don't have a strong edge) which
  // read as "our take is weak" instead of "here's the data, decide
  // yourself." Reasoning is the confidence signal now.
  const marketLabel = String(play.type || '').toUpperCase();
  return (
    <View style={styles.verdict}>
      {marketLabel && (
        <View style={[styles.verdictTierPill, {backgroundColor: C.border + '22', flexDirection:'row', alignItems:'center'}]}>
          <Text style={[styles.verdictTierText, {color: C.textMuted}]}>{marketLabel}</Text>
        </View>
      )}
      <Text style={styles.verdictPlay}>{label}</Text>
      {sub ? <Text style={styles.verdictWhy}>{scrubSourceNames(sub)}</Text> : null}
    </View>
  );
}

// ─── LOSING-MARKET CONTEXT CHIPS ────────────────────────────────────────
// Surfaces signals that fired on the losing side of a market (e.g., Rockies
// ATS_cold_season fires FADE-home-spread but HOME_RL still wins the RL
// market → the ATS signal was doing its job, just outvoted). Rendered as
// muted informational chips, NOT as picks. Only shows on markets where
// runner-up signals actually fired (empty array on primary_play = no
// render).
//
// Data source: primary_play._losing_market_notes[] built server-side by
// ensemble_scorer._score_market (2026-08-21). Each entry:
//   { market: 'ml'|'rl'|'total',
//     losing_side: 'HOME_ML'|'AWAY_RL'|'OVER'|...,
//     top_signals: [{ signal_key, class, side, contribution, prose }] }
function LosingMarketChips({ctx}: any) {
  const notes = ctx?.primary_play?._losing_market_notes;
  if (!Array.isArray(notes) || notes.length === 0) return null;
  // Filter: only render entries with at least one signal that has readable prose
  const usable = notes.filter((n: any) =>
    Array.isArray(n?.top_signals) &&
    n.top_signals.some((s: any) => s?.prose && String(s.prose).trim())
  );
  if (!usable.length) return null;

  const marketLabel: Record<string, string> = { ml: 'ML', rl: 'SPREAD', total: 'TOTAL' };

  return (
    <View style={styles.losingChipsWrap}>
      <Text style={styles.losingChipsHint}>ALSO WORTH KNOWING</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
                  contentContainerStyle={{gap: 8, paddingHorizontal: 12}}>
        {usable.flatMap((note: any) =>
          note.top_signals
            .filter((s: any) => s?.prose && String(s.prose).trim())
            .map((s: any, i: number) => (
              <View key={`${note.market}-${i}-${s.signal_key}`} style={styles.losingChip}>
                <Text style={styles.losingChipMarket}>
                  {marketLabel[note.market] || note.market.toUpperCase()}
                </Text>
                <Text style={styles.losingChipProse} numberOfLines={2}>
                  {scrubSourceNames(s.prose)}
                </Text>
              </View>
            ))
        )}
      </ScrollView>
    </View>
  );
}

// ─── JERRY READ ─────────────────────────────────────────────────────────
// Jerry = the LLM-generated per-game read. Structured markdown from
// generate_mlb_game_read.py (or equivalent per sport). Rendered as
// scrollable text w/ minimal markdown stripping — headings + bullets
// stay readable, bold/italic markers get cleaned.
//
// Placed high on the page (right after Verdict) because it's the product's
// biggest differentiator — the AI voice explaining the model reads.
function JerryReadSection({narrative, loading, synthesis}: {
  narrative?: string;
  loading?: boolean;
  synthesis?: {call_text?: string; conviction?: number; call_market?: string;
               call_side?: string; generated_at?: string};
}) {
  const [expanded, setExpanded] = useState(false);
  if (loading) {
    return (
      <View style={styles.jerrySection}>
        <View style={styles.jerryHeader}>
          <Text style={styles.jerryTitle}>🧠 JERRY'S READ</Text>
          <Text style={styles.jerryLoadingText}>reviewing the tape…</Text>
        </View>
      </View>
    );
  }
  if (!narrative || !narrative.trim()) return null;

  // Strip common markdown: `#` headings, `**bold**`, `*italic*`
  const clean = narrative
    .replace(/^\s*#{1,6}\s*/gm, '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/(?<!\*)\*(?!\*)([^\n*]+?)\*(?!\*)/g, '$1')
    .trim();

  const SHORT_LEN = 320;
  const isLong = clean.length > SHORT_LEN;
  const shown = expanded || !isLong ? clean : clean.slice(0, SHORT_LEN).trimEnd() + '…';

  // Synthesis header (Tier 2 · 2026-07-31): shows Jerry's parseable call +
  // conviction + AM/final label. Renders only when jerrySynthesis is passed
  // (i.e. we have a jerry_reads row); falls back to old header for legacy
  // jerry_cache narratives.
  const conv = synthesis?.conviction ?? 0;
  const isPass = String(synthesis?.call_market || '').toLowerCase() === 'pass';
  const chipColor = isPass ? C.textDim
                  : conv >= 75 ? C.accent
                  : conv >= 60 ? C.sharp
                  : C.warn;
  const gen = synthesis?.generated_at ? new Date(synthesis.generated_at) : null;
  const isAmRead = gen ? (gen.getUTCHours() < 17) : false;

  return (
    <View style={styles.jerrySection}>
      <View style={styles.jerryHeader}>
        <Text style={styles.jerryTitle}>🧠 JERRY'S READ</Text>
      </View>
      {/* 2026-08-20: removed the synthesis chip ({call_text · conviction}).
          Post-ensemble cutover (8/17), primary_play IS the authoritative pick
          and it's already rendered prominently in the "🔒 THE PLAY" card
          above. Showing the same tier/pick again inside JERRY'S READ was
          duplicative and confusing users (e.g., "why is there STRONG Boston
          ML twice on this screen"). Kept only the AM-read timestamp hint. */}
      {isAmRead && (
        <Text style={{color:C.textMuted,fontSize:10,fontStyle:'italic',marginBottom:8}}>
          AM read · badge shows latest ensemble pick after any recompute
        </Text>
      )}
      <Text style={styles.jerryBody}>{shown}</Text>
      {isLong && (
        <TouchableOpacity onPress={() => setExpanded(!expanded)} activeOpacity={0.7}>
          <Text style={styles.jerryToggle}>{expanded ? '▴ Show less' : '▾ Read full analysis'}</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

// ─── ALIGNMENT STRIP ─────────────────────────────────────────────────────
function AlignmentStrip({ctx}: any) {
  const align = ctx?.align_status;
  if (!align || typeof align !== 'object') return null;
  const chips: {label: string; value: string; kind: 'ok'|'warn'|'info'|'neutral'}[] = [];

  const ml = align.ml || {};
  if (ml.ext_count) {
    const side = ml.ext_lead === 'H' ? 'HOME' : ml.ext_lead === 'A' ? 'AWAY' : '—';
    chips.push({
      label: 'Handicappers',
      value: `${ml.ext_count}/${ml.ext_total} ${side}`,
      kind: ml.ext_count >= 4 ? 'ok' : 'neutral',
    });
  }
  if (ml.money_pct != null) {
    const side = ml.money_side === 'H' ? 'HOME' : ml.money_side === 'A' ? 'AWAY' : '—';
    chips.push({
      label: 'Money',
      value: `${side} ${ml.money_pct}%`,
      kind: (ml.div ?? 0) >= 10 ? 'info' : 'neutral',
    });
  }
  if (ml.lens_count) {
    const side = ml.lens_side === 'H' ? 'HOME' : ml.lens_side === 'A' ? 'AWAY' : '—';
    chips.push({
      label: 'Models',
      value: `${ml.lens_count}/${ml.lens_total} ${side}`,
      kind: ml.lens_count >= 5 ? 'ok' : 'neutral',
    });
  }
  const overall = align.overall || {};
  const verdictStr = overall.verdict || 'no_data';
  const isAligned = overall.aligned === true;
  const isDisagree = verdictStr === 'disagreement';
  chips.push({
    label: 'Overall',
    value: isAligned ? '✓ ALIGNED' : isDisagree ? '⚠ DISAGREE' : '—',
    kind: isAligned ? 'ok' : isDisagree ? 'warn' : 'neutral',
  });

  return (
    <ScrollView
      horizontal showsHorizontalScrollIndicator={false}
      style={styles.alignmentStripWrap}
      contentContainerStyle={styles.alignmentStripInner}
    >
      {chips.map((chip, i) => (
        <View key={i} style={[styles.alignChip, chipStyleFor(chip.kind)]}>
          <Text style={[styles.alignChipLabel]}>{chip.label}</Text>
          <Text style={[styles.alignChipValue, {color: chipTextColorFor(chip.kind)}]}>{chip.value}</Text>
        </View>
      ))}
    </ScrollView>
  );
}

// ─── SECTION WRAPPER ────────────────────────────────────────────────────
function Section({title, hint, children}: any) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionTitleRow}>
        <Text style={styles.sectionTitle}>{title}</Text>
        {hint ? <Text style={styles.sectionHint}>{hint}</Text> : null}
      </View>
      {children}
    </View>
  );
}

function Expander({title, badge, children}: any) {
  const [open, setOpen] = useState(false);
  return (
    <View style={styles.expander}>
      <TouchableOpacity
        onPress={() => setOpen(!open)}
        style={styles.expanderSummary}
        activeOpacity={0.7}
      >
        <Text style={styles.expanderTitle}>{title}</Text>
        {badge ? <Text style={styles.expanderBadge}>{badge}</Text> : null}
        <Text style={styles.expanderChevron}>{open ? '▴' : '▾'}</Text>
      </TouchableOpacity>
      {open && <View style={styles.expanderBody}>{children}</View>}
    </View>
  );
}

// ─── MARKET ROW ─────────────────────────────────────────────────────────
function MarketRow({closeSpread, closeTotal, homeML, awayML}: any) {
  return (
    <View style={styles.marketRow}>
      <Text style={styles.marketItem}>Spread <Text style={styles.marketVal}>{f(closeSpread, 1)}</Text></Text>
      <Text style={styles.marketItem}>Total <Text style={styles.marketVal}>{f(closeTotal, 1)}</Text></Text>
      <Text style={styles.marketItem}>ML <Text style={styles.marketVal}>{fmtOdds(awayML)}/{fmtOdds(homeML)}</Text></Text>
    </View>
  );
}

// ─── SCORE RANGE (per user "no blind average") ───────────────────────────
// 2026-09-01: reviewer-safety probe. Mirrors ScoreRange field probes
// (kept in sync with addPred / addPredHA below). Returns true if at
// least one model can build a home+away pair, false otherwise.
function hasAnyPredictedScore(ctx: any): boolean {
  const mc = safeJSON(ctx?.mc_probabilities) || {};
  // total+margin form (both required to derive H/A points)
  const totalMarginPairs = [
    [ctx?.panel_implied_total, ctx?.panel_implied_margin],
    [ctx?.jerry_pred_total,    ctx?.jerry_pred_spread],
    [ctx?.projected_total,     ctx?.projected_spread],
    [ctx?.model_pred_total,    ctx?.model_pred_spread],
    [mc.mc_expected_total ?? mc.mc_mean_total, mc.mc_expected_margin],
  ];
  if (totalMarginPairs.some(([t, m]: any) => t != null && m != null)) return true;
  // home/away points form (both required)
  const hoAwayPairs = [
    [ctx?.model_pred_home_points, ctx?.model_pred_away_points],
    [ctx?.sp_plus_pred_home_pts,  ctx?.sp_plus_pred_away_pts],
    [ctx?.eff_pred_home_pts,      ctx?.eff_pred_away_pts],
    [ctx?.elo_pred_home_pts,      ctx?.elo_pred_away_pts],
  ];
  return hoAwayPairs.some(([h, a]: any) => h != null && a != null);
}

function ScoreRange({ctx, awayTeam, homeTeam}: any) {
  const mc = safeJSON(ctx?.mc_probabilities) || {};
  const preds: {name: string; a: number; h: number}[] = [];
  const addPred = (name: string, tot: any, mgn: any) => {
    if (tot == null || mgn == null) return;
    const t = parseFloat(tot); const m = parseFloat(mgn);
    if (!isFinite(t) || !isFinite(m)) return;
    preds.push({name, a: (t - m) / 2, h: (t + m) / 2});
  };
  const addPredHA = (name: string, homePts: any, awayPts: any) => {
    // Sports that ship home/away points directly (NCAAF: model_pred_home_points,
    // sp_plus_pred_home_pts) instead of total+margin. Convert to the same shape.
    if (homePts == null || awayPts == null) return;
    const h = parseFloat(homePts); const a = parseFloat(awayPts);
    if (!isFinite(h) || !isFinite(a)) return;
    preds.push({name, a, h});
  };
  // MLB lens columns
  addPred('Panel', ctx?.panel_implied_total, ctx?.panel_implied_margin);
  addPred('Jerry', ctx?.jerry_pred_total, ctx?.jerry_pred_spread);
  addPred('v3',    ctx?.projected_total,   ctx?.projected_spread);
  addPred('v4',    ctx?.model_pred_total,  ctx?.model_pred_spread);
  addPred('MC',    mc.mc_expected_total ?? mc.mc_mean_total, mc.mc_expected_margin);
  // 2026-08-25 — cross-sport predicted-score fields so this component
  // renders for NCAAF / NFL / NBA / NCAAB, not just MLB. Each sport's
  // context builder writes its own naming; we probe all of them.
  addPredHA('Model',    ctx?.model_pred_home_points, ctx?.model_pred_away_points);
  addPredHA('SP+',      ctx?.sp_plus_pred_home_pts,   ctx?.sp_plus_pred_away_pts);
  addPredHA('Efficiency', ctx?.eff_pred_home_pts,     ctx?.eff_pred_away_pts);
  // NBA/NHL Elo — if ctx exposes projected points from elo, use those too.
  addPredHA('Elo',      ctx?.elo_pred_home_pts,       ctx?.elo_pred_away_pts);

  // 2026-09-01: reviewer safety — was rendering "No score projections
  // available" text inside the Predicted Score Section. Parent now
  // gates via hasAnyPredictedScore(). This is defense-in-depth.
  if (preds.length === 0) return null;

  const aMin = Math.min(...preds.map(p => p.a)); const aMax = Math.max(...preds.map(p => p.a));
  const hMin = Math.min(...preds.map(p => p.h)); const hMax = Math.max(...preds.map(p => p.h));
  const totMin = Math.min(...preds.map(p => p.a + p.h));
  const totMax = Math.max(...preds.map(p => p.a + p.h));
  const line = ctx?.close_total;
  const overCount = preds.filter(p => line != null && (p.a + p.h) > line).length;
  const underCount = preds.filter(p => line != null && (p.a + p.h) < line).length;
  const totalDir = overCount > underCount ? 'OVER' : underCount > overCount ? 'UNDER' : 'PUSH';
  const jerry = preds.find(p => p.name === 'Jerry');

  return (
    <View>
      <View style={styles.scoreLine}>
        <View style={styles.scoreTeam}>
          <Text style={[styles.scoreRuns, {color: C.away}]}>{f(aMin, 1)}–{f(aMax, 1)}</Text>
          <Text style={styles.scoreTeamAbbr}>{abbrev3(awayTeam)}</Text>
        </View>
        <Text style={styles.scoreSep}>—</Text>
        <View style={styles.scoreTeam}>
          <Text style={[styles.scoreRuns, {color: C.home}]}>{f(hMin, 1)}–{f(hMax, 1)}</Text>
          <Text style={styles.scoreTeamAbbr}>{abbrev3(homeTeam)}</Text>
        </View>
      </View>
      <Text style={styles.scoreSub}>
        Total range {f(totMin, 1)}–{f(totMax, 1)}
        {line != null ? ` · Line ${f(line, 1)} → ${preds.length}/${preds.length} models agree ` : ''}
        {line != null && <Text style={{color: totalDir === 'OVER' ? C.accent : C.sharp, fontWeight: '700'}}>{totalDir}</Text>}
      </Text>
      {jerry ? (
        <View style={styles.jerryBanner}>
          <Text style={styles.jerryLabel}>Top lens (Jerry):</Text>
          <Text style={styles.jerryValue}>
            {abbrev3(awayTeam)} {f(jerry.a, 1)} — {f(jerry.h, 1)} {abbrev3(homeTeam)}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

// ─── STAT PROJECTIONS (MLB slot) ────────────────────────────────────────
function StatProjectionsMLB({ctx}: any) {
  if (!ctx) return null;
  return (
    <View>
      <View style={styles.pitcherMatchup}>
        <PitcherCard
          name={ctx.away_pitcher || 'Away TBD'}
          side="away"
          k={ctx.away_pitcher_projected_ks}
          er={ctx.away_pitcher_projected_er}
          bb={ctx.away_pitcher_projected_bb}
          h={ctx.away_pitcher_projected_hits}
          outs={ctx.away_pitcher_projected_outs}
        />
        <PitcherCard
          name={ctx.home_pitcher || 'Home TBD'}
          side="home"
          k={ctx.home_pitcher_projected_ks}
          er={ctx.home_pitcher_projected_er}
          bb={ctx.home_pitcher_projected_bb}
          h={ctx.home_pitcher_projected_hits}
          outs={ctx.home_pitcher_projected_outs}
        />
      </View>
      {(ctx.panel_implied_margin != null && ctx.panel_implied_total != null) && (
        <View style={styles.teamProjBanner}>
          <Text style={styles.teamProjLabel}>Team offense (projected)</Text>
          <Text style={styles.teamProjValue}>
            {abbrev3(ctx.away_team)} {f((ctx.panel_implied_total - ctx.panel_implied_margin) / 2, 1)} R
            {' · '}
            {abbrev3(ctx.home_team)} {f((ctx.panel_implied_total + ctx.panel_implied_margin) / 2, 1)} R
          </Text>
        </View>
      )}
    </View>
  );
}

function PitcherCard({name, side, k, er, bb, h, outs}: any) {
  const ipDisplay = outs != null ? ` (${f(outs / 3, 1)} IP)` : '';
  return (
    <View style={[styles.pitcherCard, {borderTopColor: side === 'away' ? C.away : C.home}]}>
      <Text style={styles.pitcherName} numberOfLines={1}>{name}</Text>
      <Text style={styles.pitcherStats}>
        K <Text style={styles.pitcherStatBold}>{f(k, 1)}</Text>{'  '}
        ER <Text style={styles.pitcherStatBold}>{f(er, 1)}</Text>{'  '}
        BB <Text style={styles.pitcherStatBold}>{f(bb, 1)}</Text>
      </Text>
      <Text style={styles.pitcherStats}>
        H <Text style={styles.pitcherStatBold}>{f(h, 1)}</Text>{'  '}
        Outs <Text style={styles.pitcherStatBold}>{f(outs, 0)}</Text>{ipDisplay}
      </Text>
    </View>
  );
}

// ─── MONEY FLOW ─────────────────────────────────────────────────────────
// 2026-08-09: sport-aware "RL/Spread" label. MLB uses "Run Line", NBA/NFL/
// NCAAF/NCAAB/NHL use "Spread", UFC has no spread market.
const RL_LABEL_BY_SPORT: Record<string,string> = {
  MLB: 'Run Line', NFL: 'Spread', NCAAF: 'Spread',
  NBA: 'Spread', NCAAB: 'Spread', NHL: 'Puck Line',
};
const rlLabel = (sport?: string) => RL_LABEL_BY_SPORT[sport || ''] || 'Spread';

// 2026-09-01: Adapter that projects splits_summary → the shape MoneyMarket
// expects ({pick, money, bets, div, agree}). Motivation: the old code read
// oddscrowd_snapshot which is fed by align_status_common with a `source=eq.so`
// filter, so Cleatz (CZ) and Fadereport (FR) data landed in Supabase but
// never reached this card. Reading splits_summary directly gets all sources
// that splits_v2_pipeline aggregates (OC + FR + CZ + SO where present).
// Falls back to oddscrowd_snapshot if splits_summary absent (backwards compat
// during rollout; can deprecate once every ctx has splits_summary populated).
function _sideFromAgg(agg: any): {money: number; bets: number; div: number} | null {
  if (!agg || typeof agg !== 'object') return null;
  const money = typeof agg.money_pct_avg === 'number' ? agg.money_pct_avg : null;
  const bets  = typeof agg.bets_pct_avg  === 'number' ? agg.bets_pct_avg  : null;
  if (money == null && bets == null) return null;
  let div = typeof agg.divergence_avg === 'number' ? agg.divergence_avg : null;
  if (div == null && money != null && bets != null) div = Math.round(money - bets);
  return {money: money ?? 0, bets: bets ?? 0, div: div ?? 0};
}
function _marketFromSummary(mktObj: any): any | null {
  if (!mktObj || typeof mktObj !== 'object') return null;
  const sides = ['HOME', 'AWAY', 'OVER', 'UNDER'];
  let best: {side: string; money: number; bets: number; div: number; agree: number} | null = null;
  for (const s of sides) {
    if (!(s in mktObj)) continue;
    const agg = _sideFromAgg(mktObj[s]);
    if (!agg) continue;
    const agree = typeof mktObj[s]?.sources_agree === 'number' ? mktObj[s].sources_agree : 0;
    if (!best || agg.money > best.money) {
      best = {side: s, ...agg, agree};
    }
  }
  if (!best) return null;
  return {pick: best.side, money: best.money, bets: best.bets, div: best.div, agree: best.agree};
}
function oddsFromSummary(summary: any): {ml: any; rl: any; total: any} | null {
  if (!summary || typeof summary !== 'object') return null;
  // splits_summary uses 'ml'/'rl'/'total' + also 'spread'/'moneyline' variants
  const ml    = _marketFromSummary(summary.ml)   || _marketFromSummary(summary.moneyline);
  const rl    = _marketFromSummary(summary.rl)   || _marketFromSummary(summary.spread);
  const total = _marketFromSummary(summary.total);
  if (!ml && !rl && !total) return null;
  return {ml, rl, total};
}

function MoneyFlow({ctx, sport}: any) {
  // Prefer splits_summary (multi-source aggregate). Fallback to
  // oddscrowd_snapshot for ctxs that haven't been re-aggregated yet.
  const fromSummary = oddsFromSummary(ctx?.splits_summary);
  const src = fromSummary || (ctx?.oddscrowd_snapshot as any);
  if (!src || typeof src !== 'object') {
    // No source attribution. When money data is missing, we say nothing about
    // provenance (competitive moat — see feedback re: Action Network model).
    // Hidden rather than "no data" copy since presence is itself a signal.
    return null;
  }
  const markets: {key: 'ml'|'rl'|'total'; label: string; data: any}[] = [
    {key: 'ml', label: 'Moneyline', data: src.ml},
    {key: 'rl', label: rlLabel(sport), data: src.rl},
    {key: 'total', label: 'Total', data: src.total},
  ].filter(x => x.data);
  return (
    <View style={{gap: 8}}>
      {markets.map(m => <MoneyMarket key={m.key} label={m.label} data={m.data} />)}
    </View>
  );
}

function MoneyMarket({label, data}: any) {
  if (!data) return null;
  const div = data.div ?? 0;
  const sharp = div >= 10;
  const money = Math.max(0, Math.min(100, data.money ?? 0));
  const bets = Math.max(0, Math.min(100, data.bets ?? 0));
  return (
    <View style={[
      styles.moneyMarket,
      sharp && {borderLeftColor: C.sharp, backgroundColor: C.sharpDim},
    ]}>
      <View style={styles.moneyMarketHeader}>
        <Text style={styles.moneyMarketLabel}>{label}</Text>
        <View style={{flexDirection: 'row', alignItems: 'center', gap: 6}}>
          <Text style={styles.moneyMarketSide}>{data.pick || '—'}</Text>
          {sharp ? (
            <View style={styles.sharpBadge}>
              <Text style={styles.sharpBadgeText}>SHARP</Text>
            </View>
          ) : null}
          <Text style={styles.moneyMarketDiv}>{div >= 0 ? `+${div}` : div}pp</Text>
        </View>
      </View>
      <View style={{gap: 5}}>
        <MoneyBar label="Money" pct={money} color={C.sharp} />
        <MoneyBar label="Bets" pct={bets} color={C.warn} />
      </View>
      {sharp && (
        <Text style={styles.moneyDivNote}>
          <Text style={{color: C.sharp, fontWeight: '700'}}>+{div}pp sharp divergence</Text>
          {' · '}money loading on {data.pick} while public sits out
        </Text>
      )}
    </View>
  );
}

function MoneyBar({label, pct, color}: any) {
  return (
    <View style={styles.moneyBarRow}>
      <Text style={[styles.moneyBarLabel, {color}]}>{label}</Text>
      <View style={styles.moneyBarTrack}>
        <View style={[styles.moneyBarFill, {width: `${pct}%`, backgroundColor: color}]} />
      </View>
      <Text style={styles.moneyBarPct}>{pct}%</Text>
    </View>
  );
}

// ─── LINE MOVEMENT STRIP (opening → current) ─────────────────────────────
function LineMovementStrip({ctx, historicalOdds}: any) {
  const openSp = ctx?.open_spread ?? historicalOdds?.opening_spread;
  const openTot = ctx?.open_total ?? historicalOdds?.opening_total;
  const openHomeML = historicalOdds?.opening_ml_home;
  const closeSp = ctx?.close_spread;
  const closeTot = ctx?.close_total;
  const closeHomeML = ctx?.home_ml_close ?? ctx?.close_home_ml;

  const items = [
    {label: 'Spread', open: openSp, current: closeSp, fmt: (v: any) => f(v, 1)},
    {label: 'Total', open: openTot, current: closeTot, fmt: (v: any) => f(v, 1)},
    {label: 'ML (Home)', open: openHomeML, current: closeHomeML, fmt: (v: any) => v == null ? '—' : String(v)},
  ];

  return (
    <View style={styles.lineMoveStrip}>
      {items.map((it, i) => {
        const openN = typeof it.open === 'number' ? it.open : parseFloat(it.open);
        const currN = typeof it.current === 'number' ? it.current : parseFloat(it.current);
        const delta = isFinite(openN) && isFinite(currN) ? currN - openN : null;
        const deltaColor = delta == null ? C.textDim : delta === 0 ? C.textDim : delta > 0 ? C.accent : C.fade;
        return (
          <View key={i} style={styles.lineMoveItem}>
            <Text style={styles.lineMoveLabel}>{it.label}</Text>
            <Text style={styles.lineMoveValues}>
              <Text style={{color: C.textMuted}}>{it.fmt(it.open)}</Text>
              <Text style={{color: C.textDim, fontSize: 10}}>  →  </Text>
              <Text style={{color: C.text, fontWeight: '700'}}>{it.fmt(it.current)}</Text>
            </Text>
            <Text style={[styles.lineMoveDelta, {color: deltaColor}]}>
              {delta == null ? '—' : delta === 0 ? 'flat' : delta > 0 ? `↑ +${delta.toFixed(1)}` : `↓ ${delta.toFixed(1)}`}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

// ─── LENS GRID ──────────────────────────────────────────────────────────
// 2026-08-22: popover-width bug — Explainer inside each flex:1 lens cell
// rendered its help text INSIDE the cell (~60-80px wide on mobile), so
// tapping JERRY / PANEL / MC produced a tall+narrow column of text that
// looked bad. Fix: lift the "which lens is open" state to LensGrid,
// render the lens header as a plain tappable (no popover), and put ONE
// full-width popover row BELOW the grid that shows the open lens's help.
// One popover open at a time (tap same lens to close, tap different lens
// to switch). Same UX as before, actually readable.
import {explain as _explainGlossary} from '../lib/glossary';

// 2026-09-01: reviewer-safety probe. Mirrors LensGrid row-building
// (kept in sync with the same field list). Returns true if at least
// one lens has margin or total; false when the grid would render
// entirely dashes.
function hasAnyLensValue(ctx: any, gamesSport: string): boolean {
  const mc = safeJSON(ctx?.mc_probabilities) || {};
  const candidates = gamesSport === 'MLB'
    ? [ctx?.panel_implied_margin, ctx?.panel_implied_total,
       ctx?.jerry_pred_spread, ctx?.jerry_pred_total,
       ctx?.projected_spread, ctx?.projected_total,
       ctx?.model_pred_spread, ctx?.model_pred_total,
       mc.mc_expected_margin, mc.mc_expected_total, mc.mc_mean_total]
    : gamesSport === 'NCAAF'
    ? [ctx?.projected_spread, ctx?.projected_total,
       ctx?.model_pred_spread, ctx?.model_pred_total,
       mc.mc_expected_margin, mc.mc_expected_total, mc.mc_mean_total,
       ctx?.signal_confluence_net]
    : [ctx?.projected_spread, ctx?.projected_total,
       ctx?.model_pred_spread, ctx?.model_pred_total,
       ctx?.signal_confluence_net];
  return candidates.some(v => v != null);
}

function LensGrid({ctx, gamesSport}: any) {
  const mc = safeJSON(ctx?.mc_probabilities) || {};
  // 2026-09-01: NCAAF gets MC lens too — mirrors NFL/MLB. Simulator
  // populates mc_probabilities via mlb_pipeline/ncaaf_mc_simulator.py
  // (schema in 20260901h_ncaaf_mc_column.sql, workflow step in
  // .github/workflows/ncaaf_pipeline.yml after game_context build).
  // Same lens chip shape so cross-sport rendering stays uniform.
  const rows = gamesSport === 'MLB' ? [
    {name: 'Panel', m: ctx?.panel_implied_margin, t: ctx?.panel_implied_total},
    {name: 'Jerry', m: ctx?.jerry_pred_spread, t: ctx?.jerry_pred_total},
    {name: 'v3', m: ctx?.projected_spread, t: ctx?.projected_total},
    {name: 'v4', m: ctx?.model_pred_spread, t: ctx?.model_pred_total},
    {name: 'MC', m: mc.mc_expected_margin, t: mc.mc_expected_total ?? mc.mc_mean_total},
  ] : gamesSport === 'NCAAF' ? [
    {name: 'v3', m: ctx?.projected_spread, t: ctx?.projected_total},
    {name: 'v4', m: ctx?.model_pred_spread, t: ctx?.model_pred_total},
    {name: 'MC', m: mc.mc_expected_margin, t: mc.mc_expected_total ?? mc.mc_mean_total},
    {name: 'Conf', m: ctx?.signal_confluence_net, t: null},
  ] : [
    // Other non-MLB sports have fewer lens fields
    {name: 'v3', m: ctx?.projected_spread, t: ctx?.projected_total},
    {name: 'v4', m: ctx?.model_pred_spread, t: ctx?.model_pred_total},
    {name: 'Conf', m: ctx?.signal_confluence_net, t: null},
  ];

  const closeTot = ctx?.close_total;
  const [openLens, setOpenLens] = useState<string | null>(null);
  const openHelp = openLens ? _explainGlossary(openLens.toUpperCase()) : null;

  // 2026-09-01: reviewer safety — if every lens is null on both
  // margin AND total (thin ctx: UFC / sparse NHL / NCAAF FCS), the
  // grid rendered as a row of "—" tiles which reads as broken. Bail.
  const hasAnyValue = rows.some((r: any) => r.m != null || r.t != null);
  if (!hasAnyValue) return null;

  return (
    <View>
      <View style={styles.lensGrid}>
        {rows.map((r, i) => {
          const mgnSide = signSide(r.m);
          const totDir = r.t != null && closeTot != null
            ? (r.t > closeTot ? 'O' : r.t < closeTot ? 'U' : '=')
            : null;
          const missing = r.m == null;
          const isOpen = openLens === r.name.toUpperCase();
          const nameUp = r.name.toUpperCase();
          const helpAvailable = !!_explainGlossary(nameUp);
          return (
            <TouchableOpacity
              key={i}
              activeOpacity={helpAvailable ? 0.7 : 1}
              onPress={() => helpAvailable && setOpenLens(isOpen ? null : nameUp)}
              style={[
                styles.lens,
                {borderTopColor: missing ? C.border : sideColor(mgnSide), opacity: missing ? 0.5 : 1},
                isOpen && {backgroundColor: C.accent + '18'},
              ]}
            >
              <View style={{flexDirection:'row', alignItems:'center', gap:2}}>
                <Text style={[styles.lensName, isOpen && {color: C.accent}]}>{nameUp}</Text>
                {helpAvailable && (
                  <Text style={{color: (isOpen ? C.accent : C.textMuted) + 'CC', fontSize:8, fontWeight:'700'}}>ⓘ</Text>
                )}
              </View>

              <Text style={[styles.lensMargin, {color: missing ? C.textDim : sideColor(mgnSide)}]}>
                {missing ? '—' : (r.m > 0 ? `+${f(r.m, 2)}` : f(r.m, 2))}
              </Text>
              <Text style={[styles.lensTotal, {
                color: totDir === 'O' ? C.accent : totDir === 'U' ? C.sharp : C.textMuted,
              }]}>
                {r.t == null ? '—' : `${totDir ?? '='} ${f(r.t, 1)}`}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>
      {openLens && openHelp && (
        <View style={{
          marginTop: 6, paddingVertical: 8, paddingHorizontal: 12,
          backgroundColor: C.accent + '12', borderRadius: 6,
          borderLeftWidth: 2, borderLeftColor: C.accent,
        }}>
          <Text style={{color: C.textMuted, fontSize:10, fontWeight:'700', letterSpacing:0.5, marginBottom:3}}>
            {openLens} lens
          </Text>
          <Text style={{color: C.text, fontSize:12, lineHeight:17}}>{openHelp}</Text>
        </View>
      )}
    </View>
  );
}

// ─── HANDICAPPERS ROW ───────────────────────────────────────────────────
function HandicappersRow({picks, homeTeam, awayTeam, sport, records = {}}: any) {
  const nonOC = (picks || []).filter((p: any) => p.source !== 'oddscrowd');
  // 2026-09-01: hard defense — parent Section is gated but if HandicappersRow
  // is ever mounted with no non-OC picks, render nothing rather than the
  // empty-state text that read as "we forgot to build this."
  if (nonOC.length === 0) return null;
  const ml = nonOC.filter((p: any) => p.surface === 'ml');
  const rl = nonOC.filter((p: any) => p.surface === 'rl');
  const totals = nonOC.filter((p: any) => p.surface === 'total');

  const mlHome = ml.filter((p: any) => p.pick_side === 'HOME');
  const mlAway = ml.filter((p: any) => p.pick_side === 'AWAY');
  const rlHome = rl.filter((p: any) => p.pick_side === 'HOME');
  const rlAway = rl.filter((p: any) => p.pick_side === 'AWAY');
  const totOver = totals.filter((p: any) => p.pick_side === 'OVER');
  const totUnder = totals.filter((p: any) => p.pick_side === 'UNDER');

  const chip = (p: any, i: number) => {
    // Look up this source's 30d record on this surface
    const rec = records[`${p.source}|${p.surface}`] || records[`${p.source}|ALL`];
    const w = rec?.n_wins ?? 0;
    const l = rec?.n_losses ?? 0;
    const hasRec = (w + l) >= 5;
    const isHot = hasRec && rec?.hit_rate != null && Number(rec.hit_rate) >= 58;
    const isCold = hasRec && rec?.hit_rate != null && Number(rec.hit_rate) <= 42;
    // Boost/fade flag OR hot/cold record can color the chip. Record-based
    // coloring wins if it disagrees (real perf > ingest heuristic).
    const showBoost = isHot || (!hasRec && p.fade_flag === 'boost');
    const showFade  = isCold || (!hasRec && p.fade_flag === 'fade');
    return (
      <View
        key={`${p.source}-${i}`}
        style={[
          styles.handiChip,
          showBoost && {backgroundColor: C.accentDim, borderColor: C.accent},
          showFade && {backgroundColor: C.fadeDim, borderColor: C.fade},
        ]}>
        <Text style={[
          styles.handiChipText,
          showBoost && {color: C.accent},
          showFade && {color: C.fade},
        ]}>
          {personaFor(p.source)}
        </Text>
        {hasRec && (
          <Text style={[
            styles.handiChipRecord,
            showBoost && {color: C.accent},
            showFade && {color: C.fade},
          ]}>
            {w}-{l}
          </Text>
        )}
      </View>
    );
  };

  const bucketRow = (label: string, items: any[]) => (
    <View style={styles.handiRow}>
      <Text style={styles.handiSideLabel}>{label}</Text>
      {items.length === 0
        ? <Text style={styles.handiEmpty}>— none —</Text>
        : items.map(chip)}
      <Text style={styles.handiCount}>{items.length}</Text>
    </View>
  );

  return (
    <View>
      {(mlHome.length + mlAway.length) > 0 && (
        <>
          <Text style={styles.handiGroupLabel}>Moneyline</Text>
          {bucketRow(`On ${abbrev3(homeTeam)} (H)`, mlHome)}
          {bucketRow(`On ${abbrev3(awayTeam)} (A)`, mlAway)}
        </>
      )}
      {(rlHome.length + rlAway.length) > 0 && (
        <>
          <Text style={styles.handiGroupLabel}>{rlLabel(sport)}</Text>
          {bucketRow(`On ${abbrev3(homeTeam)} (H)`, rlHome)}
          {bucketRow(`On ${abbrev3(awayTeam)} (A)`, rlAway)}
        </>
      )}
      {(totOver.length + totUnder.length) > 0 && (
        <>
          <Text style={styles.handiGroupLabel}>Total</Text>
          {bucketRow('OVER', totOver)}
          {bucketRow('UNDER', totUnder)}
        </>
      )}
      {/* 2026-09-01: parent Section is now gated on nonOC>0 upstream,
          so this branch shouldn't fire in prod. Kept as defense in
          depth — if HandicappersRow is ever mounted with no non-OC
          picks (edge case, standalone testing), return null instead
          of the confusing "not pulled yet" copy. */}
    </View>
  );
}

// ─── RECENT SCHEDULE ────────────────────────────────────────────────────
// 2026-09-01: First surface of the rolling-rollup architecture
// (project_rolling_rollup_architecture_901). Reads from the universal
// `team_recent_games` matview (supabase/migrations/20260901_team_recent_games_matview.sql)
// which unions {sport}_game_results into a team-perspective per-game row.
//
// Three sub-tabs: away / H2H / home. Cross-sport by design — same
// component renders MLB, NCAAF, and (future) NFL/NBA/NCAAB/NHL by
// filtering on sport. No client-side computation of records; matview
// is the single source of truth.
//
// Renders nothing when either team has zero rows (pre-season or matview
// not yet refreshed). Silent empty state — better than a placeholder.
function RecentScheduleCard({sport, homeTeam, awayTeam}: any) {
  const [awayRows, setAwayRows] = React.useState<any[]>([]);
  const [homeRows, setHomeRows] = React.useState<any[]>([]);
  const [h2hRows,  setH2hRows]  = React.useState<any[]>([]);
  const [tab, setTab] = React.useState<'away'|'h2h'|'home'>('away');
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    const client = sb();
    if (!client || !sport || !homeTeam || !awayTeam) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      const [awayR, homeR, h2hR] = await Promise.all([
        client.from('team_recent_games')
          .select('*').eq('sport', sport).eq('team', awayTeam)
          .order('seq', {ascending: true}).limit(5),
        client.from('team_recent_games')
          .select('*').eq('sport', sport).eq('team', homeTeam)
          .order('seq', {ascending: true}).limit(5),
        client.from('team_recent_games')
          .select('*').eq('sport', sport).eq('team', homeTeam).eq('opp', awayTeam)
          .order('game_date', {ascending: false}).limit(5),
      ]);
      if (cancelled) return;
      setAwayRows(Array.isArray(awayR?.data) ? awayR.data : []);
      setHomeRows(Array.isArray(homeR?.data) ? homeR.data : []);
      setH2hRows(Array.isArray(h2hR?.data) ? h2hR.data : []);
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [sport, homeTeam, awayTeam]);

  // Silent hide when we have nothing to show for either team AND no H2H
  if (!loading && awayRows.length === 0 && homeRows.length === 0 && h2hRows.length === 0) {
    return null;
  }

  const rows = tab === 'away' ? awayRows : tab === 'home' ? homeRows : h2hRows;
  const rowsSorted = tab === 'h2h' ? rows : rows;  // already ordered by matview seq

  return (
    <View style={{gap: 10}}>
      {/* Sub-tabs */}
      <View style={rsStyles.tabBar}>
        <TabPill label={abbrev3(awayTeam)} active={tab==='away'} onPress={() => setTab('away')} />
        <TabPill label="H2H"               active={tab==='h2h'}  onPress={() => setTab('h2h')} />
        <TabPill label={abbrev3(homeTeam)} active={tab==='home'} onPress={() => setTab('home')} />
      </View>

      {/* Column header */}
      <View style={rsStyles.headRow}>
        <Text style={[rsStyles.hCol, {flex: 0.9}]}>DATE</Text>
        <Text style={[rsStyles.hCol, {flex: 1.6, textAlign: 'left'}]}>OPP</Text>
        <Text style={[rsStyles.hCol, {flex: 1.4}]}>SCORE</Text>
        <Text style={[rsStyles.hCol, {flex: 1.0}]}>ATS</Text>
        <Text style={[rsStyles.hCol, {flex: 1.0}]}>O/U</Text>
      </View>

      {/* Rows */}
      {rowsSorted.length === 0 ? (
        <Text style={rsStyles.empty}>
          {tab === 'h2h' ? 'No prior head-to-head' : 'No games logged yet this season'}
        </Text>
      ) : (
        rowsSorted.map((r, i) => <RecentGameRow key={r.game_id || i} row={r} />)
      )}

      {loading && <Text style={rsStyles.empty}>Loading…</Text>}
    </View>
  );
}

function TabPill({label, active, onPress}: any) {
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.7} style={[
      rsStyles.tabPill,
      active && rsStyles.tabPillActive,
    ]}>
      <Text style={[rsStyles.tabPillText, active && rsStyles.tabPillTextActive]}>
        {label}
      </Text>
    </TouchableOpacity>
  );
}

function RecentGameRow({row}: any) {
  const isHome = !!row.is_home;
  const isNeutral = !!row.is_neutral;
  const opp = row.opp || '—';
  const scoreUs = row.score_us;
  const scoreThem = row.score_them;
  // total_score can be null on older MLB rows — fall back to sum
  const totalScore = row.total_score != null ? row.total_score
                   : (scoreUs != null && scoreThem != null) ? (Number(scoreUs) + Number(scoreThem))
                   : null;
  const wonSU = row.won;
  const spreadRes = row.spread_result;   // 'won' | 'lost' | 'push' | null
  const totalRes  = row.total_result;    // 'over' | 'under' | 'push' | null
  const spreadLine = row.spread_line;
  const totalLine = row.total_line;

  // Compact date "MM/DD"
  let dateShort = '';
  try {
    const d = new Date(row.game_date);
    if (!isNaN(d.getTime())) dateShort = `${d.getMonth()+1}/${d.getDate()}`;
  } catch {}

  const venuePrefix = isNeutral ? 'vs' : isHome ? 'vs' : '@';
  const scoreText = (scoreUs != null && scoreThem != null) ? `${scoreUs}-${scoreThem}` : '—';

  return (
    <View style={rsStyles.dataRow}>
      <Text style={[rsStyles.cell, {flex: 0.9, color: C.textMuted}]}>{dateShort}</Text>
      <Text style={[rsStyles.cell, {flex: 1.6, textAlign: 'left'}]} numberOfLines={1}>
        <Text style={{color: C.textMuted}}>{venuePrefix} </Text>
        <Text style={{color: C.text, fontWeight: '700'}}>{abbrev3(opp)}</Text>
        {isNeutral ? <Text style={{color: C.textMuted, fontSize: 9}}>  N</Text> : null}
      </Text>
      {/* Score chip w/ W/L color. 2026-09-01: flattened nested Text —
          prior nested <Text style={{fontWeight:'700'}}> had no explicit
          color, and RN's inheritance through conditional-falsy style
          arrays isn't reliable → score rendered black on dark background
          for games w/ null won. Combining into one Text ensures the
          semantic color (win/loss/muted) applies to the whole string. */}
      <View style={{flex: 1.4, alignItems: 'center'}}>
        <View style={[
          rsStyles.chip,
          wonSU === true  && rsStyles.chipWin,
          wonSU === false && rsStyles.chipLoss,
        ]}>
          {/* 2026-09-01 v4: text stays bright cream ALWAYS. See RecordPill
              rationale — colored text on tinted bg = muddy contrast that
              reads as "black text." Background alone signals win/loss. */}
          <Text style={rsStyles.chipText}>
            {(wonSU === true ? 'W ' : wonSU === false ? 'L ' : '') + scoreText}
          </Text>
        </View>
      </View>
      {/* ATS chip */}
      <View style={{flex: 1.0, alignItems: 'center'}}>
        {spreadRes ? (
          <View style={[
            rsStyles.chip,
            spreadRes === 'won'  && rsStyles.chipWin,
            spreadRes === 'lost' && rsStyles.chipLoss,
            spreadRes === 'push' && rsStyles.chipPush,
          ]}>
            <Text style={rsStyles.chipText}>
              {spreadLine != null ? (Number(spreadLine) > 0 ? '+' : '') + spreadLine : '—'}
            </Text>
          </View>
        ) : <Text style={rsStyles.dashCell}>—</Text>}
      </View>
      {/* O/U chip */}
      <View style={{flex: 1.0, alignItems: 'center'}}>
        {totalRes ? (
          <View style={[
            rsStyles.chip,
            (totalRes === 'over' || totalRes === 'under') && rsStyles.chipPush,
          ]}>
            <Text style={[rsStyles.chipText, {color: C.textDim}]}>
              {totalRes === 'over' ? 'O ' : totalRes === 'under' ? 'U ' : ''}
              {totalLine != null ? totalLine : '—'}
            </Text>
          </View>
        ) : <Text style={rsStyles.dashCell}>—</Text>}
      </View>
    </View>
  );
}

const rsStyles = StyleSheet.create({
  tabBar: {
    flexDirection: 'row',
    backgroundColor: C.surfaceAlt,
    borderRadius: 999,
    padding: 3,
    borderWidth: 1,
    borderColor: C.borderSoft,
  },
  tabPill: {
    flex: 1,
    paddingVertical: 7,
    borderRadius: 999,
    alignItems: 'center',
  },
  tabPillActive: {
    backgroundColor: C.surface,
    shadowColor: '#000', shadowOpacity: 0.15, shadowRadius: 2, shadowOffset: {width: 0, height: 1},
  },
  tabPillText: {
    color: C.textMuted, fontSize: 12, fontWeight: '700', letterSpacing: 0.02,
  },
  tabPillTextActive: {
    color: C.text,
  },
  headRow: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 6, paddingHorizontal: 4,
    borderBottomWidth: 1, borderBottomColor: C.borderSoft,
  },
  hCol: {
    // 2026-09-01 v3: user reported black text 3x. Going NUCLEAR — use
    // C.text (bright cream #e6ebef) for every semi-label position.
    // Zero muted tokens in these three new cards anywhere.
    color: C.text, fontSize: 10, fontWeight: '700', opacity: 0.7,
    letterSpacing: 0.06, textAlign: 'center',
  },
  dataRow: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 7, paddingHorizontal: 4,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.borderSoft,
  },
  cell: {
    fontSize: 12, color: C.text, textAlign: 'center',
  },
  chip: {
    paddingHorizontal: 6, paddingVertical: 3,
    borderRadius: 999,
    backgroundColor: C.surfaceAlt,
    minWidth: 38, alignItems: 'center',
  },
  chipText: {
    // 2026-09-01 v3: bright text as default; per-condition overrides
    // still apply (win/loss/push tint) but base state is legible.
    fontSize: 11, fontWeight: '700', color: C.text, letterSpacing: 0.02,
  },
  chipWin:  {backgroundColor: C.win  + '22'},
  chipLoss: {backgroundColor: C.loss + '22'},
  chipPush: {backgroundColor: C.surfaceAlt},
  dashCell: {color: C.text, opacity: 0.6, fontSize: 12},
  empty: {
    color: C.textMuted, fontSize: 12, fontStyle: 'italic',
    textAlign: 'center', paddingVertical: 10,
  },
});


// ─── SITUATIONAL RECORDS ────────────────────────────────────────────────
// 2026-09-01: Second surface of the rolling-rollup architecture. Reads
// from team_situational_records (long-format matview populated by
// refresh_team_situational_records — see 20260901b migration).
//
// Sub-tabs per user directive: switch between Spread / Total / Moneyline
// while showing the same 4 record filters (Overall, L10, Home/Away,
// Fav/Dog) for both teams side-by-side. Wins/losses semantics per
// market:
//   spread: wins = team covered
//   total:  wins = game went OVER (from team's games)
//   ml:     wins = SU wins
//
// Uses the matview's `filter` dimension without any per-sport branches.
// When a filter has 0 games for a team (e.g. NCAAF team never played as
// underdog), renders "—" gracefully.
function SituationalCard({sport, homeTeam, awayTeam, season}: any) {
  const [awayRecs, setAwayRecs] = React.useState<any[]>([]);
  const [homeRecs, setHomeRecs] = React.useState<any[]>([]);
  const [market, setMarket] = React.useState<'spread'|'total'|'ml'>('spread');
  const [loading, setLoading] = React.useState(true);
  // 2026-09-01: prior-season blend flag. When current season has < 5
  // games in the overall spread filter for either team, we fall back
  // to prior season data + display a badge so users know it's not
  // this-season sample. Threshold N=5 mirrors the rank-chip sample floor.
  //
  // Badge is REMOTELY killable via feature_flags (no app update needed).
  // To disable for a sport: INSERT INTO feature_flags (sport, feature,
  // enabled) VALUES ('NCAAF', 'situational_prior_season_badge', false).
  // Default = enabled. Table already loaded by app on startup; we do a
  // one-row fetch here so the component stays self-contained rather
  // than prop-drilling the featureFlags map through GameDetailV2.
  const [usingPriorSeason, setUsingPriorSeason] = React.useState<number | null>(null);
  const [badgeEnabled, setBadgeEnabled] = React.useState<boolean>(true);
  const seasonForQuery = Number(season) || new Date().getFullYear();

  // Feature-flag check for the prior-season badge (one-time on mount)
  React.useEffect(() => {
    const client = sb();
    if (!client || !sport) return;
    let cancelled = false;
    (async () => {
      try {
        const {data} = await client.from('feature_flags')
          .select('enabled')
          .eq('sport', sport)
          .eq('feature', 'situational_prior_season_badge')
          .maybeSingle();
        if (cancelled) return;
        // Default enabled unless explicit false in DB
        if (data && (data as any).enabled === false) setBadgeEnabled(false);
      } catch {}
    })();
    return () => { cancelled = true; };
  }, [sport]);

  React.useEffect(() => {
    const client = sb();
    if (!client || !sport || !homeTeam || !awayTeam) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      const [awayR, homeR] = await Promise.all([
        client.from('team_situational_records')
          .select('*').eq('sport', sport).eq('team', awayTeam).eq('season', seasonForQuery),
        client.from('team_situational_records')
          .select('*').eq('sport', sport).eq('team', homeTeam).eq('season', seasonForQuery),
      ]);
      if (cancelled) return;
      let ar = Array.isArray(awayR?.data) ? awayR.data : [];
      let hr = Array.isArray(homeR?.data) ? homeR.data : [];
      // Sample-size gauge: overall spread record (proxy for total game count)
      const _gamesFor = (rows: any[]) => {
        const overall = rows.find(r => r.market === 'spread' && r.filter === 'overall');
        return overall ? (Number(overall.games) || 0) : 0;
      };
      const awayGames = _gamesFor(ar);
      const homeGames = _gamesFor(hr);
      const priorNeeded = (awayGames < 5 || homeGames < 5) && seasonForQuery > 2020;
      if (priorNeeded) {
        const prev = seasonForQuery - 1;
        const [aP, hP] = await Promise.all([
          client.from('team_situational_records')
            .select('*').eq('sport', sport).eq('team', awayTeam).eq('season', prev),
          client.from('team_situational_records')
            .select('*').eq('sport', sport).eq('team', homeTeam).eq('season', prev),
        ]);
        if (cancelled) return;
        const arP = Array.isArray(aP?.data) ? aP.data : [];
        const hrP = Array.isArray(hP?.data) ? hP.data : [];
        // Use prior only if it actually has data (avoid showing empty)
        if (arP.length > 0 || hrP.length > 0) {
          ar = arP; hr = hrP;
          setUsingPriorSeason(prev);
        } else {
          setUsingPriorSeason(null);
        }
      } else {
        setUsingPriorSeason(null);
      }
      setAwayRecs(ar); setHomeRecs(hr);
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [sport, homeTeam, awayTeam, seasonForQuery]);

  // Filter both team record arrays for the active market
  const awayByFilter = React.useMemo(() => {
    const m: Record<string, any> = {};
    awayRecs.filter((r: any) => r.market === market).forEach((r: any) => { m[r.filter] = r; });
    return m;
  }, [awayRecs, market]);
  const homeByFilter = React.useMemo(() => {
    const m: Record<string, any> = {};
    homeRecs.filter((r: any) => r.market === market).forEach((r: any) => { m[r.filter] = r; });
    return m;
  }, [homeRecs, market]);

  // Silent hide when both teams have zero records
  if (!loading && awayRecs.length === 0 && homeRecs.length === 0) {
    return null;
  }

  // Row spec: [rowLabel_left, filter_for_away, rowLabel_right, filter_for_home]
  const rows: [string, string, string, string][] = [
    ['Overall',   'overall', 'Overall',   'overall'],
    ['Last 10',   'l10',     'Last 10',   'l10'],
    ['Away',      'road',    'Home',      'home'],
    ['As Dog',    'as_dog',  'As Fav',    'as_fav'],
  ];

  return (
    <View style={{gap: 10}}>
      {/* 2026-09-01: prior-season badge when sample too thin for
          current season (<5 games). Prevents "1-0" early-season noise
          from displacing prior season's real signal (5-7 etc).
          Remotely killable via feature_flags — set enabled=false to
          hide without app update. Auto-expires ~Week 5-6 of season
          when N crosses threshold naturally. */}
      {usingPriorSeason && badgeEnabled && (
        <View style={{
          backgroundColor: C.warnDim, borderRadius: 8,
          paddingVertical: 6, paddingHorizontal: 10,
          borderLeftWidth: 3, borderLeftColor: C.warn,
        }}>
          <Text style={{color: C.warn, fontSize: 10, fontWeight: '800', letterSpacing: 0.06}}>
            EARLY {seasonForQuery} SEASON · SHOWING {usingPriorSeason} RECORDS
          </Text>
          <Text style={{color: C.text, fontSize: 11, marginTop: 2, opacity: 0.85}}>
            Current-season sample too thin (&lt;5 games). Prior season shown until enough games log.
          </Text>
        </View>
      )}

      {/* Market segmented control */}
      <View style={rsStyles.tabBar}>
        <TabPill label={sport === 'MLB' ? 'Run Line' : 'Spread'} active={market==='spread'} onPress={() => setMarket('spread')} />
        <TabPill label="Total"     active={market==='total'}  onPress={() => setMarket('total')} />
        <TabPill label="Moneyline" active={market==='ml'}     onPress={() => setMarket('ml')} />
      </View>

      {/* Team header */}
      <View style={sitStyles.teamHead}>
        <Text style={sitStyles.teamHeadName}>{abbrev3(awayTeam)}</Text>
        <Text style={sitStyles.teamHeadName}>{abbrev3(homeTeam)}</Text>
      </View>

      {/* Filter rows */}
      {rows.map(([labelL, filterA, labelR, filterH], i) => (
        <SitRow
          key={i}
          leftLabel={labelL}  leftRec={awayByFilter[filterA]}
          rightLabel={labelR} rightRec={homeByFilter[filterH]}
          market={market}
        />
      ))}

      {loading && <Text style={rsStyles.empty}>Loading…</Text>}
    </View>
  );
}

function SitRow({leftLabel, leftRec, rightLabel, rightRec, market}: any) {
  return (
    <View style={sitStyles.row}>
      <View style={sitStyles.side}>
        <Text style={sitStyles.rowLabel}>{leftLabel}</Text>
        <RecordPill rec={leftRec} market={market} />
      </View>
      <View style={sitStyles.side}>
        <Text style={[sitStyles.rowLabel, {textAlign: 'right'}]}>{rightLabel}</Text>
        <RecordPill rec={rightRec} market={market} />
      </View>
    </View>
  );
}

function RecordPill({rec, market}: any) {
  if (!rec || rec.games === 0 || rec.games == null) {
    return <View style={sitStyles.pillEmpty}><Text style={sitStyles.pillEmptyText}>—</Text></View>;
  }
  const w = Number(rec.wins) || 0;
  const l = Number(rec.losses) || 0;
  const p = Number(rec.pushes) || 0;
  const total = w + l;  // pushes excluded from hit%
  const hitPct = total > 0 ? Math.round((w / total) * 100) : 0;
  // Color the pill by hit%: >=58 green (hot), <=42 red (cold), else neutral
  const tint = total >= 5 ? (
    hitPct >= 58 ? 'win' : hitPct <= 42 ? 'loss' : 'neutral'
  ) : 'neutral';
  const label = market === 'total'
    ? `${w}-${l}${p ? `-${p}` : ''}`      // O-U-P
    : `${w}-${l}${p ? `-${p}` : ''}`;      // W-L-P
  // 2026-09-01 v4: text stays bright cream ALWAYS. Prior version set
  // text color to C.loss (red) on C.loss+22 background (light red) —
  // contrast between red-on-red rendered as muddy/dark (user reported
  // 3x as "black text hard to see" on 5-7, 4-6, 0-5 loss records).
  // Same issue for win (green-on-green). Fix: only the BACKGROUND
  // conveys win/loss. Text = bright cream on tinted bg = high contrast.
  return (
    <View style={[
      sitStyles.pill,
      tint === 'win'  && sitStyles.pillWin,
      tint === 'loss' && sitStyles.pillLoss,
    ]}>
      <Text style={sitStyles.pillText}>{label}</Text>
    </View>
  );
}

const sitStyles = StyleSheet.create({
  teamHead: {
    flexDirection: 'row', justifyContent: 'space-between',
    paddingHorizontal: 4, paddingBottom: 4,
    borderBottomWidth: 1, borderBottomColor: C.borderSoft,
  },
  teamHeadName: {
    color: C.text, fontSize: 13, fontWeight: '800', letterSpacing: 0.06,
  },
  row: {
    flexDirection: 'row', justifyContent: 'space-between',
    paddingVertical: 8, paddingHorizontal: 4,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.borderSoft,
  },
  side: {
    flex: 1, gap: 4,
  },
  rowLabel: {
    // 2026-09-01 v3: nuclear bright — C.text with opacity so it reads
    // as slightly muted but never dark.
    color: C.text, opacity: 0.75, fontSize: 10, fontWeight: '700',
    letterSpacing: 0.05, textTransform: 'uppercase',
  },
  pill: {
    paddingVertical: 8, paddingHorizontal: 12,
    borderRadius: 8, backgroundColor: C.surfaceAlt,
    alignItems: 'center',
    marginRight: 8,
  },
  pillWin:  {backgroundColor: C.win  + '22'},
  pillLoss: {backgroundColor: C.loss + '22'},
  pillText: {
    color: C.text, fontSize: 15, fontWeight: '800',
    letterSpacing: 0.02,
  },
  pillEmpty: {
    paddingVertical: 8, paddingHorizontal: 12,
    borderRadius: 8, backgroundColor: 'transparent',
    borderWidth: 1, borderColor: C.borderSoft, borderStyle: 'dashed',
    alignItems: 'center', marginRight: 8,
  },
  pillEmptyText: {
    color: C.text, opacity: 0.6, fontSize: 13, fontWeight: '600',
  },
});


// ─── TEAM STATS ─────────────────────────────────────────────────────────
// 2026-09-01: Third surface of the rolling-rollup architecture. Reads
// from team_stats_rolling matview (populated by refresh_team_stats_rolling
// — see 20260901c migration). Kills the NCAAFTeamMatchupCard client-side
// compute + fuzzy substring matching anti-patterns identified in the
// 9/1 audit.
//
// Design (matches user's Action Network reference + directive to show
// RAW stats + rank together, e.g. "258 yd · 12th"):
//   - Sub-tab: Offense / Defense
//   - Sport-agnostic — same component for NCAAF, MLB (when populated),
//     NFL (when populated). Coverage today: NCAAF only.
//   - Each row: stat display_label, away team raw+rank cell, home team
//     raw+rank cell
//   - Rank chip color-coded by quintile (top 20% = elite green, next
//     20% good-cyan, mid = neutral, next 20% pale-red, bottom 20% = red)
//   - SP+ overall shown as a header banner above the sub-tabs (composite
//     rating that spans offense + defense)
//   - Silent hide when team_stats_rolling returns 0 for both teams
function TeamStatsCard({sport, homeTeam, awayTeam, season}: any) {
  const [awayStats, setAwayStats] = React.useState<any[]>([]);
  const [homeStats, setHomeStats] = React.useState<any[]>([]);
  const [side, setSide] = React.useState<'off'|'def'>('off');
  const [loading, setLoading] = React.useState(true);
  const seasonForQuery = Number(season) || new Date().getFullYear();

  React.useEffect(() => {
    const client = sb();
    if (!client || !sport || !homeTeam || !awayTeam) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      const [awayR, homeR] = await Promise.all([
        client.from('team_stats_rolling')
          .select('*').eq('sport', sport).eq('team', awayTeam).eq('season', seasonForQuery),
        client.from('team_stats_rolling')
          .select('*').eq('sport', sport).eq('team', homeTeam).eq('season', seasonForQuery),
      ]);
      if (cancelled) return;
      let ar = Array.isArray(awayR?.data) ? awayR.data : [];
      let hr = Array.isArray(homeR?.data) ? homeR.data : [];
      // Fallback prior season for pre-season (Week 1 NCAAF pattern)
      if (ar.length === 0 && hr.length === 0 && seasonForQuery > 2020) {
        const prev = seasonForQuery - 1;
        const [aP, hP] = await Promise.all([
          client.from('team_stats_rolling')
            .select('*').eq('sport', sport).eq('team', awayTeam).eq('season', prev),
          client.from('team_stats_rolling')
            .select('*').eq('sport', sport).eq('team', homeTeam).eq('season', prev),
        ]);
        if (cancelled) return;
        ar = Array.isArray(aP?.data) ? aP.data : [];
        hr = Array.isArray(hP?.data) ? hP.data : [];
      }
      setAwayStats(ar); setHomeStats(hr);
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [sport, homeTeam, awayTeam, seasonForQuery]);

  // 2026-09-01: HOOK ORDER FIX — useMemo hooks MUST run every render.
  // Prior version had `if (empty) return null` BEFORE these useMemos,
  // which crashed with "Rendered fewer hooks than expected" whenever a
  // sport has no stats data (empty → skip useMemos → next render calls
  // useMemos → hook count mismatches). Early return now lives AFTER
  // every hook.
  const awayByKey = React.useMemo(() => {
    const m: Record<string, any> = {};
    awayStats.forEach((r: any) => { m[r.stat_key] = r; });
    return m;
  }, [awayStats]);
  const homeByKey = React.useMemo(() => {
    const m: Record<string, any> = {};
    homeStats.forEach((r: any) => { m[r.stat_key] = r; });
    return m;
  }, [homeStats]);

  if (!loading && awayStats.length === 0 && homeStats.length === 0) return null;

  // Stat groups per sport. All keys resolve to rows in team_stats_rolling
  // (populated by 20260901c + 20260901f migrations). Order matters — rendered
  // top-to-bottom in the card.
  const NCAAF_OFFENSE = [
    'pass_yds_pg', 'rush_yds_pg', 'total_yds_pg',
    'third_down_pct', 'off_epa_per_play', 'off_success_rate',
    'off_explosiveness', 'sp_offense',
    'turnovers_pg', 'penalty_yds_pg',
  ];
  const NCAAF_DEFENSE = [
    'points_allowed_pg', 'sp_defense',
    'def_epa_per_play', 'def_rush_epa_allowed', 'def_success_rate_allowed',
  ];
  const NFL_OFFENSE = [
    'pass_yds_pg', 'rush_yds_pg', 'total_yds_pg',
    'pass_tds_pg', 'rush_tds_pg',
    'off_pass_epa', 'off_rush_epa',
    'ints_pg', 'sacks_suffered_pg', 'penalty_yds_pg',
  ];
  const NFL_DEFENSE = [
    'points_allowed_pg', 'yds_allowed_pg',
    'pass_yds_allowed_pg', 'rush_yds_allowed_pg',
    'def_pass_epa', 'def_rush_epa',
  ];
  // NCAAB is efficiency-driven (not per-game volumes like football); split
  // isn't offense-vs-defense in the same way. "Offense" tab shows scoring/
  // pace; "Defense" tab shows opponent-scoring/defensive rating.
  const NCAAB_OFFENSE = [
    'ppg_for', 'off_rating', 'net_rating', 'avg_margin', 'tempo',
  ];
  const NCAAB_DEFENSE = [
    'ppg_against', 'def_rating',
  ];
  // MLB — batting from mlb_team_offense; pitching from mlb_team_pitching
  // (persisted 2026-09-01, populated by mlb_team_pitching_pull.py) + bullpen.
  const MLB_OFFENSE = [
    'team_avg', 'team_obp', 'team_slg', 'team_ops',
    'team_woba', 'team_wrc_plus', 'team_iso',
    'team_bb_pct', 'team_k_pct',
    'team_runs_pg', 'team_hr_pg',
  ];
  const MLB_DEFENSE = [
    'team_era', 'team_whip', 'team_k_per_9', 'team_bb_per_9',
    'team_hr_per_9', 'team_baa',
    'bullpen_era', 'bullpen_save_pct',
  ];
  // NBA — 2026-09-01 rewrite: honest labels for PPG-based stats
  // (nba_elo writes PPG, not per-100-poss). Four-factors (efg/tov/orb/
  // ftr + opp) rows appear only once a puller populates them.
  const NBA_OFFENSE = [
    'points_pg', 'net_pts_pg', 'pace',
    'efg_pct', 'tov_pct', 'orb_pct', 'ft_rate',
  ];
  const NBA_DEFENSE = [
    'points_allowed_pg', 'opp_efg_pct', 'opp_tov_pct', 'opp_orb_pct',
  ];
  // NHL — expected-goal + special teams + possession
  const NHL_OFFENSE = [
    'xgf_per60', 'high_danger_for',
    'pp_pct', 'corsi_5v5',
  ];
  const NHL_DEFENSE = [
    'xga_per60', 'high_danger_against', 'pk_pct',
  ];
  const OFFENSE_BY_SPORT: Record<string, string[]> = {
    NCAAF: NCAAF_OFFENSE,
    NFL:   NFL_OFFENSE,
    NCAAB: NCAAB_OFFENSE,
    MLB:   MLB_OFFENSE,
    NBA:   NBA_OFFENSE,
    NHL:   NHL_OFFENSE,
  };
  const DEFENSE_BY_SPORT: Record<string, string[]> = {
    NCAAF: NCAAF_DEFENSE,
    NFL:   NFL_DEFENSE,
    NCAAB: NCAAB_DEFENSE,
    MLB:   MLB_DEFENSE,
    NBA:   NBA_DEFENSE,
    NHL:   NHL_DEFENSE,
  };

  const statKeys = side === 'off'
    ? (OFFENSE_BY_SPORT[sport] || [])
    : (DEFENSE_BY_SPORT[sport] || []);

  // Header SP+ overall banner (composite rating)
  const spOvrH = homeByKey['sp_overall'];
  const spOvrA = awayByKey['sp_overall'];

  return (
    <View style={{gap: 10}}>
      {/* SP+ overall banner. 2026-09-01: SP+ label wrapped in Explainer
          — casual users don't know what SP+ means. Tap on either side's
          label opens the glossary help inline. */}
      {(spOvrH || spOvrA) && (
        <View>
          <View style={tsStyles.spBanner}>
            <View style={tsStyles.spSide}>
              <Explainer term="SP+" color={C.textMuted} activeColor={C.accent}
                         helpColor={C.text} helpBg={C.accent + '18'}>
                <Text style={tsStyles.spLabel}>{abbrev3(awayTeam)} SP+ ⓘ</Text>
              </Explainer>
              {spOvrA ? (
                <View style={tsStyles.spRow}>
                  <Text style={tsStyles.spValue}>{spOvrA.raw_value > 0 ? '+' : ''}{spOvrA.raw_value}</Text>
                  <RankChip rank={spOvrA.rank} leagueSize={spOvrA.league_size} />
                </View>
              ) : <Text style={tsStyles.dash}>—</Text>}
            </View>
            <View style={tsStyles.spDivider} />
            <View style={tsStyles.spSide}>
              <Text style={tsStyles.spLabel}>{abbrev3(homeTeam)} SP+</Text>
              {spOvrH ? (
                <View style={tsStyles.spRow}>
                  <Text style={tsStyles.spValue}>{spOvrH.raw_value > 0 ? '+' : ''}{spOvrH.raw_value}</Text>
                  <RankChip rank={spOvrH.rank} leagueSize={spOvrH.league_size} />
                </View>
              ) : <Text style={tsStyles.dash}>—</Text>}
            </View>
          </View>
        </View>
      )}

      {/* Offense / Defense toggle */}
      <View style={rsStyles.tabBar}>
        <TabPill label="Offense" active={side==='off'} onPress={() => setSide('off')} />
        <TabPill label="Defense" active={side==='def'} onPress={() => setSide('def')} />
      </View>

      {/* Team header */}
      <View style={sitStyles.teamHead}>
        <Text style={sitStyles.teamHeadName}>{abbrev3(awayTeam)}</Text>
        <Text style={[sitStyles.teamHeadName, {textAlign: 'right'}]}>{abbrev3(homeTeam)}</Text>
      </View>

      {/* Stat rows */}
      {statKeys.map(k => (
        <StatRow key={k} statKey={k} awayRow={awayByKey[k]} homeRow={homeByKey[k]} />
      ))}

      {loading && <Text style={rsStyles.empty}>Loading…</Text>}
    </View>
  );
}

function StatRow({statKey, awayRow, homeRow}: any) {
  // Prefer whichever has display_label present (both should have same);
  // fall back to prettified stat_key.
  const label = awayRow?.display_label || homeRow?.display_label
             || statKey.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase());
  const unit = awayRow?.unit || homeRow?.unit || '';
  return (
    <View style={tsStyles.statRow}>
      <StatCell row={awayRow} unit={unit} align="right" />
      <Text style={tsStyles.statLabel}>{label}</Text>
      <StatCell row={homeRow} unit={unit} align="left" />
    </View>
  );
}

function StatCell({row, unit, align}: any) {
  if (!row || row.raw_value == null) {
    return (
      <View style={[tsStyles.statCell, align==='left' ? {alignItems: 'flex-start'} : {alignItems: 'flex-end'}]}>
        <Text style={tsStyles.dash}>—</Text>
      </View>
    );
  }
  const isRightAlign = align !== 'left';
  return (
    <View style={[
      tsStyles.statCell,
      isRightAlign ? {alignItems: 'flex-end'} : {alignItems: 'flex-start'},
    ]}>
      <View style={{
        flexDirection: isRightAlign ? 'row' : 'row-reverse',
        alignItems: 'baseline', gap: 6,
      }}>
        {/* 2026-09-01: split value + unit into sibling Text components
            instead of nesting. Nested Text inside a parent Text can lose
            explicit color inheritance in RN under certain style-array
            combinations, resulting in default-black text. Siblings each
            hold their own StyleSheet reference so color is always
            explicit. */}
        <Text style={tsStyles.statValue}>{row.raw_value}</Text>
        {unit ? <Text style={tsStyles.statUnit}> {unit}</Text> : null}
        <RankChip rank={row.rank} leagueSize={row.league_size} />
      </View>
    </View>
  );
}

function RankChip({rank, leagueSize}: any) {
  if (rank == null || leagueSize == null || leagueSize === 0) return null;
  // Quintile color: top 20% = elite, next 20% = good, mid = neutral, next 20% = poor, bottom = bad
  const pct = rank / leagueSize;
  let bg = C.surfaceAlt, fg = C.textDim;
  if (pct <= 0.20)      { bg = C.win  + '26'; fg = C.win; }
  else if (pct <= 0.40) { bg = C.sharp + '20'; fg = C.sharp; }
  else if (pct <= 0.60) { bg = C.surfaceAlt; fg = C.textDim; }
  else if (pct <= 0.80) { bg = C.warn + '22'; fg = C.warn; }
  else                  { bg = C.loss + '22'; fg = C.loss; }
  // Ordinal suffix
  const s = String(rank);
  const last = rank % 100;
  const suffix = (last >= 11 && last <= 13) ? 'th'
               : (rank % 10 === 1) ? 'st'
               : (rank % 10 === 2) ? 'nd'
               : (rank % 10 === 3) ? 'rd' : 'th';
  return (
    <View style={[tsStyles.rankChip, {backgroundColor: bg}]}>
      <Text style={[tsStyles.rankText, {color: fg}]}>{s}{suffix}</Text>
    </View>
  );
}

const tsStyles = StyleSheet.create({
  spBanner: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: C.surfaceAlt,
    borderRadius: 8, paddingVertical: 10, paddingHorizontal: 12,
    borderWidth: 1, borderColor: C.borderSoft,
  },
  spSide: {flex: 1, gap: 4, alignItems: 'center'},
  spDivider: {width: 1, alignSelf: 'stretch', backgroundColor: C.borderSoft, marginHorizontal: 12},
  spLabel: {color: C.text, opacity: 0.7, fontSize: 10, letterSpacing: 0.06, fontWeight: '700'},
  spRow: {flexDirection: 'row', alignItems: 'center', gap: 8},
  spValue: {color: C.text, fontSize: 20, fontWeight: '900', letterSpacing: -0.02},
  statRow: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 8, paddingHorizontal: 4,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.borderSoft,
  },
  statLabel: {
    flex: 1.4, textAlign: 'center',
    color: C.text, opacity: 0.75, fontSize: 10, fontWeight: '700',
    letterSpacing: 0.04, textTransform: 'uppercase',
    paddingHorizontal: 6,
  },
  statCell: {flex: 1.3, justifyContent: 'center'},
  statValue: {color: C.text, fontSize: 15, fontWeight: '800', letterSpacing: -0.01},
  statUnit: {color: C.textMuted, fontSize: 10, fontWeight: '600'},
  rankChip: {
    minWidth: 38, paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: 999, alignItems: 'center',
  },
  rankText: {fontSize: 11, fontWeight: '800', letterSpacing: 0.02},
  dash: {color: C.text, opacity: 0.6, fontSize: 13, fontWeight: '600'},
});


// ─── SPORT-SPECIFIC SLOT ─────────────────────────────────────────────────
function SportSpecificSlot({ctx, gamesSport, game}: any) {
  if (gamesSport === 'MLB') {
    // Pitcher card lives in Stat Projections above; no additional slot needed
    return null;
  }
  if (gamesSport === 'UFC') {
    // 2026-09-01: killed "coming next" placeholder. Section shipping
    // when reach/reads + method/round breakdown lands. Reviewer safety.
    return null;
  }
  if (gamesSport === 'NFL') {
    return <NFLSlot ctx={ctx} game={game} />;
  }
  if (gamesSport === 'NCAAF') {
    // 2026-08-24: NCAAF got its own slot. Previously reused NFLSlot which
    // queries nfl_team_stats + nfl_starters — those tables have no NCAAF
    // data, so every NCAAF card showed "team: season stats unavailable"
    // (the bare feeling on TCU@UNC card). NCAAFSlot surfaces what's actually
    // populated: SP+ ratings, roster physicality (OL/DL weight, class year),
    // returning production %, projected spread.
    return <NCAAFSlot ctx={ctx} game={game} />;
  }
  if (gamesSport === 'NBA') {
    return <NBASlot ctx={ctx} game={game} />;
  }
  if (gamesSport === 'NCAAB') {
    return <NCAABSlot ctx={ctx} game={game} />;
  }
  if (gamesSport === 'NHL') {
    // 2026-09-01: killed "coming next" placeholder. Goalie matchup
    // + B2B chip ships once nhl_starters / nhl_goalies land. Reviewer safety.
    return null;
  }
  return null;
}

// ─── NCAAF SLOT ──────────────────────────────────────────────────────────
// 2026-08-25 redesign (see ncaaf_slot_mock artifact): backend-driven cards
// that render present-data-only. Parent GameDetailV2 already handles
// Predicted Score, Money Flow, Line Movement, Model Consensus, External
// Handicappers as shared shells. This slot adds sport-unique cards.
//
// Backend controls what shows via ctx fields:
//   Weather:            temp / wind / dome / weather_source (via ncaaf_weather_pull.py)
//   Efficiency:         home_sp_overall / away_sp_overall / sp_gap / projected_spread
//   Rosters:            home_returning_production / ol_dl_weight_gap_home /
//                       home_ol_avg_wt / home_avg_class_year / class_year_edge_home
//   Tendencies:         home/road ATS/SU/OU/as-fav-dog from
//                       ncaaf_team_home_road_tendencies materialized view
//
// Adding a new sport-unique card = one Section entry + component.
// Adding a new field WITHIN an existing card = zero app change if the
// data lands in a JSONB blob that render loops over.
function NCAAFSlot({ctx, game}: any) {
  const homeTeam = ctx?.home_team || game?.home_team;
  const awayTeam = ctx?.away_team || game?.away_team;

  return (
    <>
      <NCAAFFCSNotice homeTeam={homeTeam} awayTeam={awayTeam} season={ctx?.season} />
      <NCAAFTeamMatchupCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NCAAFRostersRichCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <SportWeatherCard ctx={ctx} />
      {/* TeamTendenciesCard removed 2026-09-01 — Situational Records
          supersedes with cleaner sub-tab UX + universal cross-sport
          shape. Function definition kept below for rollback safety. */}
    </>
  );
}

// 2026-09-01: FCS opponent notice per user directive. NCAAF data
// (ncaaf_team_stats, ncaaf_team_defense_stats, SP+, EPA panel) is
// FBS-only via CFBD /stats/season/advanced endpoint. When either
// team in a matchup is FCS, most stat surfaces render dashes and
// model projections are unreliable (SP+ absent, spread projection
// heavily biased). Rather than let the user wonder why the card is
// thin, tell them upfront.
//
// Detection: probe team_stats_rolling — if either team has 0 rows
// for the season, they're not in ncaaf_team_stats → FCS.
function NCAAFFCSNotice({homeTeam, awayTeam, season}: any) {
  const [fcs, setFcs] = React.useState<{home: boolean; away: boolean; loading: boolean}>({home: false, away: false, loading: true});
  React.useEffect(() => {
    const client = sb();
    if (!client || !homeTeam || !awayTeam) return;
    let cancelled = false;
    (async () => {
      const seasonInt = Number(season) || new Date().getFullYear();
      // Try current season first, fall back to prior (Week 1 pattern)
      const seasons = [seasonInt, seasonInt - 1];
      for (const s of seasons) {
        const [h, a] = await Promise.all([
          client.from('team_stats_rolling')
            .select('team').eq('sport', 'NCAAF').eq('team', homeTeam).eq('season', s).limit(1),
          client.from('team_stats_rolling')
            .select('team').eq('sport', 'NCAAF').eq('team', awayTeam).eq('season', s).limit(1),
        ]);
        if (cancelled) return;
        const homeMissing = !Array.isArray(h?.data) || h.data.length === 0;
        const awayMissing = !Array.isArray(a?.data) || a.data.length === 0;
        // If BOTH have data (or one does), use this season's result
        if (!homeMissing || !awayMissing) {
          setFcs({home: homeMissing, away: awayMissing, loading: false});
          return;
        }
        // Both missing — try prior season
      }
      // Both missing across both seasons → both FCS
      if (!cancelled) setFcs({home: true, away: true, loading: false});
    })();
    return () => { cancelled = true; };
  }, [homeTeam, awayTeam, season]);

  if (fcs.loading) return null;
  if (!fcs.home && !fcs.away) return null;

  const which = fcs.home && fcs.away ? `Both teams (${abbrev3(awayTeam)}, ${abbrev3(homeTeam)})`
              : fcs.home ? homeTeam
              : awayTeam;
  return (
    <View style={{
      backgroundColor: C.warnDim, borderRadius: 10, padding: 12,
      borderLeftWidth: 3, borderLeftColor: C.warn, marginBottom: 8,
    }}>
      <Text style={{color: C.warn, fontSize: 11, fontWeight: '800', letterSpacing: 0.06, marginBottom: 3}}>
        FCS OPPONENT · LIMITED MODEL COVERAGE
      </Text>
      <Text style={{color: C.text, fontSize: 12, lineHeight: 17}}>
        {which} is not tracked in our efficiency model (SP+, EPA, success rate — all FBS-only).
        Team Stats and Model Consensus will show limited data.
        Recent Schedule + Situational Records still render from box-score history.
      </Text>
    </View>
  );
}

// ─── NCAAF TEAM MATCHUP (rich — matches MLB PitcherCard density) ────────
// Side-by-side team cards with efficiency numbers, advantage highlighting,
// and a bottom "model read" strip. Fetches ncaaf_team_stats directly so
// SP+ Offense/Defense/traditional stats render even when the ctx row is
// thin (pre-season / EPA not yet computed). Falls back to prior season
// stats when current season has no rows yet.
function NCAAFTeamMatchupCard({ctx, homeTeam, awayTeam}: any) {
  const [stats, setStats] = React.useState<{home?: any; away?: any}>({});
  const [seasonUsed, setSeasonUsed] = React.useState<number | null>(null);
  React.useEffect(() => {
    const client = sb();
    if (!client || !homeTeam || !awayTeam) return;
    (async () => {
      const currentSeason = Number(ctx?.season) || new Date().getFullYear();
      const trySeasons = [currentSeason, currentSeason - 1];
      // 2026-08-29: when props' homeTeam/awayTeam are Odds-API mascot
      // names ("Virginia Cavaliers") that don't match CFBD short names
      // ("Virginia"), the exact `.in('team', [...])` misses. Do a
      // per-season pull of ALL teams and fuzzy-match by substring so
      // we always resolve a row when the data exists.
      for (const s of trySeasons) {
        const {data} = await client.from('ncaaf_team_stats')
          .select('*').eq('season', s);
        if (Array.isArray(data) && data.length > 0) {
          const _norm = (n: string) => (n || '').toLowerCase().replace(/\s+/g, ' ').trim();
          const hNorm = _norm(homeTeam);
          const aNorm = _norm(awayTeam);
          const homeRow = data.find((r: any) => {
            const t = _norm(r.team);
            return t === hNorm || hNorm.includes(t) || t.includes(hNorm);
          });
          const awayRow = data.find((r: any) => {
            const t = _norm(r.team);
            return t === aNorm || aNorm.includes(t) || t.includes(aNorm);
          });
          const anyReal = [homeRow, awayRow].some((r: any) => r &&
            (r.sp_overall != null || r.off_epa_per_play != null || r.points_per_game != null));
          if (anyReal) {
            setStats({home: homeRow, away: awayRow});
            setSeasonUsed(s);
            return;
          }
        }
      }
    })();
  }, [homeTeam, awayTeam, ctx?.season]);

  // Prefer live team_stats fetch, fall back to ctx fields.
  const spH = stats.home?.sp_overall ?? ctx?.home_sp_overall;
  const spA = stats.away?.sp_overall ?? ctx?.away_sp_overall;
  const spOffH = stats.home?.sp_offense;
  const spOffA = stats.away?.sp_offense;
  const spDefH = stats.home?.sp_defense;
  const spDefA = stats.away?.sp_defense;
  const offH = stats.home?.off_epa_per_play ?? ctx?.home_off_epa_pp;
  const offA = stats.away?.off_epa_per_play ?? ctx?.away_off_epa_pp;
  const defH = stats.home?.def_epa_per_play ?? ctx?.home_def_epa_pp;
  const defA = stats.away?.def_epa_per_play ?? ctx?.away_def_epa_pp;
  const succOffH = stats.home?.off_success_rate; const succOffA = stats.away?.off_success_rate;
  const explH = stats.home?.off_explosiveness;   const explA = stats.away?.off_explosiveness;
  // 2026-08-29: raw volumetric — per-game averages computed from
  // ncaaf_team_stats totals ÷ games. Ctx exposes _pg fields when
  // games count is populated; falls back to team_stats-fetched
  // totals if ctx path is thin. Yards allowed comes from
  // ncaaf_team_defense_stats (opponent-attribution avg).
  const _pg = (val: any, g: any) => (val == null || !g) ? null : Number(val) / Number(g);
  // 2026-08-31: prefer server-computed summary blob (adds FBS-wide
  // ranks). Falls back to ctx flat fields → live-fetched raw stats.
  const homeSum = ctx?.home_team_stats_summary || null;
  const awaySum = ctx?.away_team_stats_summary || null;
  const passOffH = homeSum?.pass_yds_pg ?? ctx?.home_pass_yds_pg ?? _pg(stats.home?.pass_yards, stats.home?.games);
  const passOffA = awaySum?.pass_yds_pg ?? ctx?.away_pass_yds_pg ?? _pg(stats.away?.pass_yards, stats.away?.games);
  const rushOffH = homeSum?.rush_yds_pg ?? ctx?.home_rush_yds_pg ?? _pg(stats.home?.rush_yards, stats.home?.games);
  const rushOffA = awaySum?.rush_yds_pg ?? ctx?.away_rush_yds_pg ?? _pg(stats.away?.rush_yards, stats.away?.games);
  const passAllH = homeSum?.pass_yds_allowed_pg ?? ctx?.home_def_pass_ypg;
  const passAllA = awaySum?.pass_yds_allowed_pg ?? ctx?.away_def_pass_ypg;
  const rushAllH = homeSum?.rush_yds_allowed_pg ?? ctx?.home_def_rush_ypg;
  const rushAllA = awaySum?.rush_yds_allowed_pg ?? ctx?.away_def_rush_ypg;
  const ptsOffH  = homeSum?.pts_pg;
  const ptsOffA  = awaySum?.pts_pg;
  const ptsAllH  = homeSum?.pts_allowed_pg ?? ctx?.home_def_ppg;
  const ptsAllA  = awaySum?.pts_allowed_pg ?? ctx?.away_def_ppg;
  const spGap = ctx?.sp_gap;
  const projSpread = ctx?.projected_spread;
  const closeSpread = ctx?.close_spread;
  // Render if we have ANY stats to show — volumetric (from summary blob
  // or raw ctx), spread-projection ppg, or SP+ efficiency.
  if (spH == null && spA == null && spOffH == null && spOffA == null &&
      offH == null && offA == null &&
      passOffH == null && passOffA == null && rushOffH == null && rushOffA == null) return null;

  // Advantage helper — highlights the higher (or lower for def) number.
  const cmp = (a?: number, b?: number, higherIsBetter = true) => {
    if (a == null || b == null) return {a: false, b: false};
    if (higherIsBetter) return {a: a > b, b: b > a};
    return {a: a < b, b: b < a};
  };
  const spAdv  = cmp(spA, spH, true);
  const offAdv = cmp(offA, offH, true);
  const defAdv = cmp(defA, defH, false);  // lower def EPA = better defense

  const StatRow = ({label, a, b, aAdv, bAdv, fmt}: any) => {
    if (a == null && b == null) return null;
    const fA = fmt ? fmt(a) : (a == null ? '—' : Number(a).toFixed(2));
    const fB = fmt ? fmt(b) : (b == null ? '—' : Number(b).toFixed(2));
    return (
      <View style={{flexDirection: 'row', alignItems: 'center', paddingVertical: 4}}>
        <Text style={{flex: 1, fontSize: 13, fontWeight: aAdv ? '800' : '600',
                      color: aAdv ? C.away : C.textDim, textAlign: 'left'}}>{fA}</Text>
        <Text style={{width: 78, fontSize: 10, fontWeight: '800', color: C.textMuted,
                      textAlign: 'center', letterSpacing: 0.5}}>{label}</Text>
        <Text style={{flex: 1, fontSize: 13, fontWeight: bAdv ? '800' : '600',
                      color: bAdv ? C.home : C.textDim, textAlign: 'right'}}>{fB}</Text>
      </View>
    );
  };

  const gapVal = spGap != null ? Number(spGap) : null;
  const gapFavHome = gapVal != null && gapVal > 0;
  // 2026-08-25 sign-convention fix. Prior math was
  //     projSpread - closeSpread
  // which produced garbage +15.3pt edges on the TCU-UNC card because
  // projected_spread uses "positive = home wins" but close_spread uses
  // the market convention "negative = home favored." Adding them
  // (equivalent to projSpread - (-closeSpread)) normalizes to the
  // same signed margin and yields the true home-cover edge.
  //   edge_for_home > 0 → BACK home (model gives home more than market)
  //   edge_for_home < 0 → BACK away (model gives away more than market)
  const projVsMarket = (projSpread != null && closeSpread != null)
    ? Number(projSpread) + Number(closeSpread) : null;
  const edgeSide = projVsMarket == null ? null
    : projVsMarket > 0 ? 'home'
    : projVsMarket < 0 ? 'away' : null;
  const edgeMag = projVsMarket == null ? null : Math.abs(projVsMarket);

  return (
    <Section title="Team Matchup" hint={seasonUsed ? `efficiency + EPA · ${seasonUsed} season · higher = advantage` : 'efficiency + EPA · higher = advantage'}>
      <View style={{backgroundColor: C.surface2, borderRadius: 10, padding: 12}}>
        {/* Team header row */}
        <View style={{flexDirection: 'row', alignItems: 'center', paddingBottom: 8,
                      borderBottomWidth: 1, borderBottomColor: C.border + '55'}}>
          <View style={{flex: 1}}>
            <Text style={{color: C.away, fontSize: 13, fontWeight: '800'}} numberOfLines={1}>{awayTeam}</Text>
          </View>
          <Text style={{width: 78, color: C.textMuted, fontSize: 9, fontWeight: '700',
                        textAlign: 'center', letterSpacing: 0.5}}>METRIC</Text>
          <View style={{flex: 1}}>
            <Text style={{color: C.home, fontSize: 13, fontWeight: '800', textAlign: 'right'}} numberOfLines={1}>{homeTeam}</Text>
          </View>
        </View>
        {/* 2026-08-31 reorder: volumetric (casual-friendly) rows FIRST.
            Prior order buried "PASS YPG / RUSH YPG" under 7 jargon rows
            (POWER / EPA / SUCCESS % / EXPLOSIVE) that casual users don't
            recognize. Casual bettors read the top-of-card first, so
            surface "gives up 189 rush ypg" before "off_epa_per_play 0.15".
            Now with FBS-wide rank chips from server summary blob. */}
        {(ptsOffH != null || ptsOffA != null) && (
          <RankedStatRow label="POINTS/G" a={ptsOffA} b={ptsOffH}
                         aRank={awaySum?.rank_scoring_off} bRank={homeSum?.rank_scoring_off} higherIsBetter />
        )}
        {(ptsAllH != null || ptsAllA != null) && (
          <RankedStatRow label="PTS ALLOW" a={ptsAllA} b={ptsAllH}
                         aRank={awaySum?.rank_scoring_def} bRank={homeSum?.rank_scoring_def} />
        )}
        <RankedStatRow label="PASS YDS/G" a={passOffA} b={passOffH}
                       aRank={awaySum?.rank_pass_off} bRank={homeSum?.rank_pass_off} higherIsBetter />
        <RankedStatRow label="PASS ALLOW" a={passAllA} b={passAllH}
                       aRank={awaySum?.rank_pass_def} bRank={homeSum?.rank_pass_def} />
        <RankedStatRow label="RUSH YDS/G" a={rushOffA} b={rushOffH}
                       aRank={awaySum?.rank_rush_off} bRank={homeSum?.rank_rush_off} higherIsBetter />
        <RankedStatRow label="RUSH ALLOW" a={rushAllA} b={rushAllH}
                       aRank={awaySum?.rank_rush_def} bRank={homeSum?.rank_rush_def} />
        {(spOffH != null || spOffA != null) && (
          <StatRow label="PROJ PPG"   a={spOffA} b={spOffH}
                   aAdv={spOffA != null && spOffH != null && spOffA > spOffH}
                   bAdv={spOffA != null && spOffH != null && spOffH > spOffA}
                   fmt={(v: any) => v == null ? '—' : Number(v).toFixed(1)} />
        )}
        {(spDefH != null || spDefA != null) && (
          <StatRow label="PROJ PA"    a={spDefA} b={spDefH}
                   aAdv={spDefA != null && spDefH != null && spDefA < spDefH}
                   bAdv={spDefA != null && spDefH != null && spDefH < spDefA}
                   fmt={(v: any) => v == null ? '—' : Number(v).toFixed(1)} />
        )}

        {/* Advanced metrics — separator + subhead to signal "this is
            handicapper-grade stuff, casual bettors can skip." */}
        {(spH != null || spA != null || offH != null || offA != null) && (
          <View style={{marginTop: 10, paddingTop: 8, borderTopWidth: 1, borderTopColor: C.border + '55'}}>
            <Text style={{color: C.textMuted, fontSize: 9, fontWeight: '800',
                          letterSpacing: 0.6, marginBottom: 4, textAlign: 'center'}}>ADVANCED METRICS</Text>
            <StatRow label="POWER"      a={spA} b={spH} aAdv={spAdv.a} bAdv={spAdv.b}
                     fmt={(v: any) => v == null ? '—' : Number(v).toFixed(1)} />
            <StatRow label="OFF EPA/PL" a={offA} b={offH} aAdv={offAdv.a} bAdv={offAdv.b}
                     fmt={(v: any) => v == null ? '—' : Number(v).toFixed(3)} />
            <StatRow label="DEF EPA/PL" a={defA} b={defH} aAdv={defAdv.a} bAdv={defAdv.b}
                     fmt={(v: any) => v == null ? '—' : Number(v).toFixed(3)} />
            <StatRow label="SUCCESS %"  a={succOffA} b={succOffH}
                     aAdv={succOffA != null && succOffH != null && succOffA > succOffH}
                     bAdv={succOffA != null && succOffH != null && succOffH > succOffA}
                     fmt={(v: any) => v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`} />
            <StatRow label="EXPLOSIVE"  a={explA} b={explH}
                     aAdv={explA != null && explH != null && explA > explH}
                     bAdv={explA != null && explH != null && explH > explA}
                     fmt={(v: any) => v == null ? '—' : Number(v).toFixed(2)} />
          </View>
        )}

        {/* Model read banner — mirrors MLB teamProjBanner. Speaks
            projected margin + market comparison in the same signed
            frame so nothing double-counts across sign conventions. */}
        {(projSpread != null) && (
          <View style={{marginTop: 10, padding: 10, backgroundColor: C.accent + '14',
                        borderRadius: 8, borderLeftWidth: 3, borderLeftColor: C.accent}}>
            <Text style={{color: C.accent, fontSize: 10, fontWeight: '800', letterSpacing: 0.6, marginBottom: 4}}>MODEL READ</Text>
            <Text style={{color: C.text, fontSize: 12, lineHeight: 17}}>
              Model projects <Text style={{fontWeight: '800', color: Number(projSpread) > 0 ? C.home : C.away}}>{Number(projSpread) > 0 ? homeTeam : awayTeam}</Text> by <Text style={{fontWeight: '800', color: C.text}}>{Math.abs(Number(projSpread)).toFixed(1)}</Text>.
              {closeSpread != null && (
                <Text> Market has {Number(closeSpread) < 0 ? homeTeam : awayTeam} laying <Text style={{fontWeight: '800', color: C.text}}>{Math.abs(Number(closeSpread)).toFixed(1)}</Text>.</Text>
              )}
              {edgeMag != null && edgeSide && (
                <Text>{'\n'}Edge: <Text style={{fontWeight: '800', color: edgeMag >= 1 ? C.accent : C.textMuted}}>{edgeMag.toFixed(1)} pts on {edgeSide === 'home' ? homeTeam : awayTeam}</Text>
                  {edgeMag >= 2 ? ' — real value.' : edgeMag >= 1 ? ' — slight lean.' : ' — market is right in line.'}
                </Text>
              )}
            </Text>
          </View>
        )}
      </View>
    </Section>
  );
}

// ─── NCAAF ROSTERS (rich card matching Team Matchup density) ────────────
function NCAAFRostersRichCard({ctx, homeTeam, awayTeam}: any) {
  const rpH = ctx?.home_returning_production;
  const rpA = ctx?.away_returning_production;
  const olH = ctx?.home_ol_avg_wt; const olA = ctx?.away_ol_avg_wt;
  const clsH = ctx?.home_avg_class_year; const clsA = ctx?.away_avg_class_year;
  const olGapH = ctx?.ol_dl_weight_gap_home; const olGapA = ctx?.ol_dl_weight_gap_away;
  const classEdge = ctx?.class_year_edge_home;
  if (rpH == null && rpA == null && olH == null && clsH == null) return null;

  const cmp = (a?: number, b?: number, higherIsBetter = true) => {
    if (a == null || b == null) return {a: false, b: false};
    if (higherIsBetter) return {a: a > b, b: b > a};
    return {a: a < b, b: b < a};
  };
  const rpAdv = cmp(rpA, rpH, true);
  const olAdv = cmp(olA, olH, true);
  const clsAdv = cmp(clsA, clsH, true);

  const StatRow = ({label, a, b, aAdv, bAdv, fmt}: any) => {
    if (a == null && b == null) return null;
    return (
      <View style={{flexDirection: 'row', alignItems: 'center', paddingVertical: 4}}>
        <Text style={{flex: 1, fontSize: 13, fontWeight: aAdv ? '800' : '600',
                      color: aAdv ? C.away : C.textDim, textAlign: 'left'}}>{fmt(a)}</Text>
        <Text style={{width: 96, fontSize: 10, fontWeight: '800', color: C.textMuted,
                      textAlign: 'center', letterSpacing: 0.5}}>{label}</Text>
        <Text style={{flex: 1, fontSize: 13, fontWeight: bAdv ? '800' : '600',
                      color: bAdv ? C.home : C.textDim, textAlign: 'right'}}>{fmt(b)}</Text>
      </View>
    );
  };

  const notes: string[] = [];
  if (olGapH != null && Number(olGapH) >= 15)
    notes.push(`${(homeTeam || '').split(' ').pop()} OL outweighs opposing DL by ${Math.round(Number(olGapH))} lb — ground-game leverage.`);
  if (olGapA != null && Number(olGapA) >= 15)
    notes.push(`${(awayTeam || '').split(' ').pop()} OL outweighs opposing DL by ${Math.round(Number(olGapA))} lb.`);
  if (classEdge != null && Math.abs(Number(classEdge)) >= 0.3)
    notes.push(`${Number(classEdge) > 0 ? (homeTeam || '').split(' ').pop() : (awayTeam || '').split(' ').pop()} carries a class-year experience edge (Weeks 1-3 significant).`);

  return (
    <Section title="Rosters &amp; Continuity" hint="returning production + physicality">
      <View style={{backgroundColor: C.surface2, borderRadius: 10, padding: 12}}>
        <View style={{flexDirection: 'row', alignItems: 'center', paddingBottom: 8,
                      borderBottomWidth: 1, borderBottomColor: C.border + '55'}}>
          <View style={{flex: 1}}>
            <Text style={{color: C.away, fontSize: 13, fontWeight: '800'}} numberOfLines={1}>{awayTeam}</Text>
          </View>
          <Text style={{width: 96, color: C.textMuted, fontSize: 9, fontWeight: '700',
                        textAlign: 'center', letterSpacing: 0.5}}>METRIC</Text>
          <View style={{flex: 1}}>
            <Text style={{color: C.home, fontSize: 13, fontWeight: '800', textAlign: 'right'}} numberOfLines={1}>{homeTeam}</Text>
          </View>
        </View>
        <StatRow label="RETURN PROD" a={rpA} b={rpH} aAdv={rpAdv.a} bAdv={rpAdv.b}
                 fmt={(v: any) => v == null ? '—' : `${Math.round(Number(v) * 100)}%`} />
        <StatRow label="OL AVG WT" a={olA} b={olH} aAdv={olAdv.a} bAdv={olAdv.b}
                 fmt={(v: any) => v == null ? '—' : `${Math.round(Number(v))} lb`} />
        <StatRow label="CLASS EXP" a={clsA} b={clsH} aAdv={clsAdv.a} bAdv={clsAdv.b}
                 fmt={(v: any) => v == null ? '—' : Number(v).toFixed(1)} />
        {notes.length > 0 && (
          <View style={{marginTop: 10, gap: 5}}>
            {notes.map((n, i) => (
              <Text key={i} style={{color: C.accent, fontSize: 11, lineHeight: 15}}>• {n}</Text>
            ))}
          </View>
        )}
      </View>
    </Section>
  );
}

// ─── SPORT WEATHER (shared shell — NCAAF/NFL/etc) ───────────────────────
// Reads temp/wind/dome from ctx (whichever sport). Hides on domes or
// when weather columns are still null (pre-pull).
function SportWeatherCard({ctx}: any) {
  const temp = ctx?.temp;
  const wind = ctx?.wind;
  const dome = ctx?.dome;
  const src  = ctx?.weather_source;
  if (dome === true) return null;                // don't waste a card on domes
  // 2026-09-01: gate on weather_source not null (only set when a real
  // pull succeeded). Prior version rendered "0°F" on games where the
  // weather column had a stale/default 0 (user report: "Akron 0°F,
  // impossible"). No weather_source = data was never actually pulled
  // for this venue → hide the card entirely.
  if (!src) return null;
  if (temp == null && wind == null) return null; // pre-pull / no coverage
  // Also hide if temp is exactly 0 and wind is 0 — that's the classic
  // "field defaulted to 0" fingerprint, not real weather.
  if ((temp === 0 || temp == null) && (wind === 0 || wind == null)) return null;
  return (
    <Section title="Weather" hint="game-time forecast">
      <View style={{flexDirection: 'row', gap: 8}}>
        {temp != null && (
          <View style={{flex: 1, backgroundColor: C.border + '22', padding: 10, borderRadius: 8, alignItems: 'center'}}>
            <Text style={{color: C.textMuted, fontSize: 10, fontWeight: '800', letterSpacing: 0.5}}>TEMP</Text>
            <Text style={{color: C.text, fontSize: 15, fontWeight: '800', marginTop: 2}}>{Math.round(Number(temp))}°F</Text>
          </View>
        )}
        {wind != null && (
          <View style={{flex: 1, backgroundColor: C.border + '22', padding: 10, borderRadius: 8, alignItems: 'center'}}>
            <Text style={{color: C.textMuted, fontSize: 10, fontWeight: '800', letterSpacing: 0.5}}>WIND</Text>
            <Text style={{color: Number(wind) >= 15 ? C.sharp : C.text, fontSize: 15, fontWeight: '800', marginTop: 2}}>{Math.round(Number(wind))} mph</Text>
          </View>
        )}
      </View>
      {wind != null && Number(wind) >= 15 && (
        <Text style={{color: C.textMuted, fontSize: 11, marginTop: 8, fontStyle: 'italic'}}>
          15+ mph correlates with lower totals historically.
        </Text>
      )}
    </Section>
  );
}

// ─── NCAAF EFFICIENCY (renamed from SP+ — no provider name in user copy) ─
function NCAAFEfficiencyCard({ctx, homeTeam, awayTeam}: any) {
  const spHome = ctx?.home_sp_overall;
  const spAway = ctx?.away_sp_overall;
  const spGap = ctx?.sp_gap;
  const projSpread = ctx?.projected_spread;
  if (spHome == null || spAway == null) return null;
  return (
    <Section title="Efficiency Ratings" hint="season-level power rating">
      <View style={{gap: 8}}>
        <View style={[styles.pitcherCard, {borderTopColor: C.away, padding: 12}]}>
          <Text style={styles.pitcherName}>{awayTeam}</Text>
          <Text style={styles.pitcherStats}>
            Overall: <Text style={styles.pitcherStatBold}>{Number(spAway).toFixed(1)}</Text>
          </Text>
        </View>
        <View style={[styles.pitcherCard, {borderTopColor: C.home, padding: 12}]}>
          <Text style={styles.pitcherName}>{homeTeam}</Text>
          <Text style={styles.pitcherStats}>
            Overall: <Text style={styles.pitcherStatBold}>{Number(spHome).toFixed(1)}</Text>
          </Text>
        </View>
        {spGap != null && projSpread != null && (
          <View style={{padding: 10, backgroundColor: C.accent + '10', borderRadius: 8, borderWidth: 1, borderColor: C.accent + '40'}}>
            <Text style={{color: C.accent, fontWeight: '800', fontSize: 11, letterSpacing: 0.5, marginBottom: 4}}>MODEL READ</Text>
            <Text style={{color: C.text, fontSize: 12}}>
              Rating gap of {Number(spGap).toFixed(1)} points {Number(spGap) > 0 ? `favors ${homeTeam}` : `favors ${awayTeam}`}. Model projects spread at {Number(projSpread).toFixed(1)}.
            </Text>
          </View>
        )}
      </View>
    </Section>
  );
}

// ─── NCAAF ROSTERS & CONTINUITY (returning + physicality consolidated) ──
function NCAAFRostersCard({ctx, homeTeam, awayTeam}: any) {
  const rpHome = ctx?.home_returning_production;
  const rpAway = ctx?.away_returning_production;
  const olGapH = ctx?.ol_dl_weight_gap_home;
  const olGapA = ctx?.ol_dl_weight_gap_away;
  const classEdge = ctx?.class_year_edge_home;
  const homeClassYr = ctx?.home_avg_class_year;
  const awayClassYr = ctx?.away_avg_class_year;
  const homeOl = ctx?.home_ol_avg_wt;
  const awayOl = ctx?.away_ol_avg_wt;
  const hasAny = rpHome != null || rpAway != null || olGapH != null || olGapA != null ||
                 classEdge != null || homeOl != null || awayOl != null;
  if (!hasAny) return null;
  const awayShort = (awayTeam || '').split(' ').pop();
  const homeShort = (homeTeam || '').split(' ').pop();
  return (
    <Section title="Rosters & Continuity" hint="returning production + physicality">
      <View style={{gap: 6}}>
        {(rpAway != null || rpHome != null) && (
          <View style={{flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4}}>
            <Text style={{color: C.textDim, fontSize: 12}}>Returning offense + defense</Text>
            <Text style={{color: C.text, fontSize: 12, fontWeight: '700'}}>
              {rpAway != null ? `${awayShort} ${Math.round(Number(rpAway) * 100)}%` : '—'}
              {' · '}
              {rpHome != null ? `${homeShort} ${Math.round(Number(rpHome) * 100)}%` : '—'}
            </Text>
          </View>
        )}
        {homeOl != null && awayOl != null && (
          <View style={{flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4}}>
            <Text style={{color: C.textDim, fontSize: 12}}>OL avg weight</Text>
            <Text style={{color: C.text, fontSize: 12, fontWeight: '700'}}>
              {awayShort} {Math.round(Number(awayOl))}lb · {homeShort} {Math.round(Number(homeOl))}lb
            </Text>
          </View>
        )}
        {homeClassYr != null && awayClassYr != null && (
          <View style={{flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4}}>
            <Text style={{color: C.textDim, fontSize: 12}}>Class experience</Text>
            <Text style={{color: C.text, fontSize: 12, fontWeight: '700'}}>
              {awayShort} {Number(awayClassYr).toFixed(1)} · {homeShort} {Number(homeClassYr).toFixed(1)}
              <Text style={{color: C.textMuted, fontSize: 10}}> (1=Fr, 4=Sr)</Text>
            </Text>
          </View>
        )}
        {((olGapH != null && Number(olGapH) >= 15) ||
          (olGapA != null && Number(olGapA) >= 15) ||
          (classEdge != null && Math.abs(Number(classEdge)) >= 0.3)) && (
          <View style={{marginTop: 4, gap: 4}}>
            {olGapH != null && Number(olGapH) >= 15 && (
              <Text style={[styles.pitcherStats, {color: C.accent}]}>
                {homeShort} OL outweighs opposing DL by {Math.round(Number(olGapH))} lb — ground-game leverage
              </Text>
            )}
            {olGapA != null && Number(olGapA) >= 15 && (
              <Text style={[styles.pitcherStats, {color: C.accent}]}>
                {awayShort} OL outweighs opposing DL by {Math.round(Number(olGapA))} lb
              </Text>
            )}
            {classEdge != null && Math.abs(Number(classEdge)) >= 0.3 && (
              <Text style={[styles.pitcherStats, {color: C.accent}]}>
                {Number(classEdge) > 0 ? homeShort : awayShort} carries a class-year experience edge (Weeks 1-3 significant)
              </Text>
            )}
          </View>
        )}
      </View>
    </Section>
  );
}

// ─── TEAM TENDENCIES (shared — NFL / NCAAF / NBA) ───────────────────────
// Reads the {sport}_team_home_road_tendencies materialized view built by
// migration 20260826b. Renders home team's home-splits vs away team's
// road-splits so a casual can see "how does this team do in this spot."
//
// Backend-driven: the METRICS array below is the display manifest. Adding
// a new column to the view + adding a row here = one narrow app update.
// The view refresh cadence lives in each sport's pipeline workflow (calls
// refresh_home_road_tendencies RPC after the resolver).
function TeamTendenciesCard({sport, ctx, homeTeam, awayTeam}: any) {
  const [homeRow, setHomeRow] = React.useState<any>(null);
  const [awayRow, setAwayRow] = React.useState<any>(null);
  const [seasonUsed, setSeasonUsed] = React.useState<number | null>(null);
  const [loading, setLoading] = React.useState(true);

  const season = ctx?.season;
  const viewName = sport === 'NFL'   ? 'nfl_team_home_road_tendencies'
                  : sport === 'NCAAF' ? 'ncaaf_team_home_road_tendencies'
                  : sport === 'NBA'   ? 'nba_team_home_road_tendencies'
                  : null;

  React.useEffect(() => {
    const client = sb();
    if (!client || !viewName || !homeTeam || !awayTeam) { setLoading(false); return; }
    (async () => {
      // Season fallback: current season may have zero graded games (pre-Wk 4
      // for football, pre-Nov 3 for basketball). If current returns nothing
      // for either team, fall back to prior season so the card renders last
      // year's baseline. Mirrors the mock's "2025 season · blends after Wk 4".
      const currentSeason = Number(season) || new Date().getFullYear();
      const trySeasons = [currentSeason, currentSeason - 1];
      let picked: number | null = null;
      let matched: any[] = [];
      for (const s of trySeasons) {
        const {data} = await client.from(viewName).select('*')
          .in('team', [homeTeam, awayTeam]).eq('season', s);
        if (Array.isArray(data) && data.length > 0) {
          matched = data;
          picked = s;
          break;
        }
      }
      const homeR = matched.find((r: any) => r.team === homeTeam);
      const awayR = matched.find((r: any) => r.team === awayTeam);
      setHomeRow(homeR || null);
      setAwayRow(awayR || null);
      setSeasonUsed(picked);
      setLoading(false);
    })();
  }, [viewName, homeTeam, awayTeam, season]);

  if (!viewName) return null;
  if (loading) return null;  // silent — no flicker
  if (!homeRow && !awayRow) return null;  // no data (offseason / backfill pending)

  // 2026-08-31 UI cleanup: hit% + color coding + advantage highlight.
  // Prior version was 4 rows of raw W-L numbers — scan-hostile. Now each
  // cell shows W-L plus % chip with green/red tint at extremes, and the
  // stronger side gets the accent so users see who wins the matchup
  // dimension at a glance.
  const HOT_PCT = 58;   // above → green tint
  const COLD_PCT = 42;  // below → red tint

  const pctOf = (w?: number, l?: number) => {
    const wn = w ?? 0; const ln = l ?? 0;
    if (wn + ln === 0) return null;
    return Math.round(1000 * wn / (wn + ln)) / 10;
  };
  const ouPct = (o?: number, u?: number) => {
    // For O/U, "hit" is whichever direction dominates — return the % of majority
    const on = o ?? 0; const un = u ?? 0;
    if (on + un === 0) return null;
    return Math.round(1000 * Math.max(on, un) / (on + un)) / 10;
  };
  const pctColor = (p?: number | null) => {
    if (p == null) return C.textDim;
    if (p >= HOT_PCT) return C.win;
    if (p <= COLD_PCT) return C.loss;
    return C.text;
  };
  const metricRows = [
    {label: 'ATS',
     aW: awayRow?.road_ats_wins, aL: awayRow?.road_ats_losses,
     hW: homeRow?.home_ats_wins, hL: homeRow?.home_ats_losses,
     type: 'wl' as const,
     hint: `${teamAbbrev(awayTeam)} away · ${teamAbbrev(homeTeam)} home`},
    {label: 'ML (SU)',
     aW: awayRow?.road_su_wins, aL: awayRow?.road_su_losses,
     hW: homeRow?.home_su_wins, hL: homeRow?.home_su_losses,
     type: 'wl' as const,
     hint: 'straight-up win rate'},
    {label: 'Total',
     aO: awayRow?.road_ou_overs, aU: awayRow?.road_ou_unders,
     hO: homeRow?.home_ou_overs, hU: homeRow?.home_ou_unders,
     type: 'ou' as const,
     hint: 'over/under trend'},
    {label: 'as Fav/Dawg',
     aFW: awayRow?.as_fav_ats_wins, aFL: awayRow?.as_fav_ats_losses,
     aDW: awayRow?.as_dog_ats_wins, aDL: awayRow?.as_dog_ats_losses,
     hFW: homeRow?.as_fav_ats_wins, hFL: homeRow?.as_fav_ats_losses,
     hDW: homeRow?.as_dog_ats_wins, hDL: homeRow?.as_dog_ats_losses,
     type: 'favdog' as const,
     hint: 'ATS record in role'},
  ];

  const StatCell = ({wl, pct, sub, teamColor}: any) => (
    <View style={{flex: 1, alignItems: 'center'}}>
      <Text style={{color: pctColor(pct), fontSize: 14, fontWeight: '800',
                    letterSpacing: -0.2, fontVariant: ['tabular-nums']}}>{wl}</Text>
      {pct != null && (
        <Text style={{color: pctColor(pct), fontSize: 10, fontWeight: '700', marginTop: 1,
                      fontVariant: ['tabular-nums']}}>{pct.toFixed(0)}%</Text>
      )}
      {sub && (
        <Text style={{color: C.textMuted, fontSize: 9, fontWeight: '600',
                      letterSpacing: 0.4, marginTop: 1}}>{sub}</Text>
      )}
    </View>
  );

  return (
    <Section title="Trends &amp; Tendencies"
             hint={seasonUsed ? `situational splits · ${seasonUsed} season` : 'situational splits'}>
      <View>
        <View style={{flexDirection: 'row', paddingHorizontal: 4, paddingBottom: 8,
                      borderBottomWidth: 0.5, borderBottomColor: C.border}}>
          <Text style={{flex: 1.4, color: C.textMuted, fontSize: 9, fontWeight: '800', letterSpacing: 0.5}}>METRIC</Text>
          <Text style={{flex: 1, color: C.away, fontSize: 9, fontWeight: '800', letterSpacing: 0.5, textAlign: 'center'}}>
            {teamAbbrev(awayTeam)}
          </Text>
          <Text style={{flex: 1, color: C.home, fontSize: 9, fontWeight: '800', letterSpacing: 0.5, textAlign: 'center'}}>
            {teamAbbrev(homeTeam)}
          </Text>
        </View>
        {metricRows.map((m, i) => {
          let aCell, hCell;
          if (m.type === 'wl') {
            const aN = (m.aW ?? 0) + (m.aL ?? 0);
            const hN = (m.hW ?? 0) + (m.hL ?? 0);
            aCell = <StatCell wl={aN ? `${m.aW ?? 0}-${m.aL ?? 0}` : '—'} pct={pctOf(m.aW, m.aL)} />;
            hCell = <StatCell wl={hN ? `${m.hW ?? 0}-${m.hL ?? 0}` : '—'} pct={pctOf(m.hW, m.hL)} />;
          } else if (m.type === 'ou') {
            const aN = (m.aO ?? 0) + (m.aU ?? 0);
            const hN = (m.hO ?? 0) + (m.hU ?? 0);
            const aWL = aN ? `${m.aO ?? 0}-${m.aU ?? 0}` : '—';
            const hWL = hN ? `${m.hO ?? 0}-${m.hU ?? 0}` : '—';
            const aSub = aN ? ((m.aO ?? 0) >= (m.aU ?? 0) ? 'OVER lean' : 'UNDER lean') : '';
            const hSub = hN ? ((m.hO ?? 0) >= (m.hU ?? 0) ? 'OVER lean' : 'UNDER lean') : '';
            aCell = <StatCell wl={aWL} pct={ouPct(m.aO, m.aU)} sub={aSub} />;
            hCell = <StatCell wl={hWL} pct={ouPct(m.hO, m.hU)} sub={hSub} />;
          } else { // favdog
            const aFN = (m.aFW ?? 0) + (m.aFL ?? 0);
            const aDN = (m.aDW ?? 0) + (m.aDL ?? 0);
            const hFN = (m.hFW ?? 0) + (m.hFL ?? 0);
            const hDN = (m.hDW ?? 0) + (m.hDL ?? 0);
            const aRole = aFN >= aDN ? 'fav' : 'dawg';
            const hRole = hFN >= hDN ? 'fav' : 'dawg';
            const aW = aRole === 'fav' ? m.aFW : m.aDW;
            const aL = aRole === 'fav' ? m.aFL : m.aDL;
            const hW = hRole === 'fav' ? m.hFW : m.hDW;
            const hL = hRole === 'fav' ? m.hFL : m.hDL;
            const aN2 = (aW ?? 0) + (aL ?? 0);
            const hN2 = (hW ?? 0) + (hL ?? 0);
            aCell = <StatCell wl={aN2 ? `${aW ?? 0}-${aL ?? 0}` : '—'} pct={pctOf(aW, aL)} sub={aN2 ? aRole.toUpperCase() : ''} />;
            hCell = <StatCell wl={hN2 ? `${hW ?? 0}-${hL ?? 0}` : '—'} pct={pctOf(hW, hL)} sub={hN2 ? hRole.toUpperCase() : ''} />;
          }
          return (
            <View key={i} style={{flexDirection: 'row', alignItems: 'center', paddingVertical: 10, paddingHorizontal: 4,
                                  borderBottomWidth: i < metricRows.length - 1 ? 0.5 : 0,
                                  borderBottomColor: C.border + '44'}}>
              <View style={{flex: 1.4}}>
                <Text style={{color: C.text, fontSize: 12, fontWeight: '700'}}>{m.label}</Text>
                {m.hint && <Text style={{color: C.textMuted, fontSize: 9, marginTop: 1}}>{m.hint}</Text>}
              </View>
              {aCell}
              {hCell}
            </View>
          );
        })}
      </View>
    </Section>
  );
}


// ─── NFL SLOT ────────────────────────────────────────────────────────────
// Phase 1 (2026-07-30) — renders what's available from nfl_game_context +
// nfl_team_stats. Phase 2 adds QB starter card + injuries + weather when
// those pipes ship.
function NFLSlot({ctx, game}: any) {
  const homeTeam = ctx?.home_team || game?.home_team;
  const awayTeam = ctx?.away_team || game?.away_team;
  return (
    <>
      <SportWeatherCard ctx={ctx} />
      <NFLQBMatchupCard  ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NFLTeamMatchupCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NFLInjuriesCard   ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      {/* TeamTendenciesCard removed 2026-09-01 — see NCAAF slot note. */}
      <NFLSituationalCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
    </>
  );
}

// ─── NFL QB MATCHUP ─────────────────────────────────────────────────────
// Buffed from the prior "Starting QBs" name-only card. Now joins Sleeper
// projections (nfl_player_projections, 802 rows live per 2026-08-25) so
// each starter shows projected fantasy pts + season Y/A when available.
function NFLQBMatchupCard({ctx, homeTeam, awayTeam}: any) {
  const [starters, setStarters] = useState<{home?: any; away?: any}>({});
  const [projections, setProjections] = useState<{home?: any; away?: any}>({});
  React.useEffect(() => {
    const client = sb();
    if (!client || !homeTeam || !awayTeam) return;
    (async () => {
      const {data: st} = await client.from('nfl_starters')
        .select('team,position,player_name,is_starter,week')
        .in('team', [homeTeam, awayTeam])
        .eq('position', 'QB')
        .eq('is_starter', true)
        .order('week', {ascending: false})
        .limit(2);
      const smap: any = {};
      for (const row of (st || [])) if (!smap[row.team]) smap[row.team] = row;
      setStarters({home: smap[homeTeam], away: smap[awayTeam]});
      const starterNames = Object.values(smap).map((s: any) => s.player_name).filter(Boolean);
      if (starterNames.length > 0) {
        const {data: proj} = await client.from('nfl_player_projections')
          .select('player_name,team,proj_fantasy_pts,proj_pass_yards,proj_pass_tds')
          .in('player_name', starterNames)
          .in('team', [homeTeam, awayTeam])
          .order('pulled_at', {ascending: false})
          .limit(4);
        const pmap: any = {};
        for (const row of (proj || [])) if (!pmap[row.team]) pmap[row.team] = row;
        setProjections({home: pmap[homeTeam], away: pmap[awayTeam]});
      }
    })();
  }, [homeTeam, awayTeam]);

  if (!starters.home && !starters.away) return null;

  const renderQB = (side: 'home' | 'away', st: any, proj: any, team: string) => (
    <View style={[styles.pitcherCard, {borderTopColor: side === 'home' ? C.home : C.away, padding: 12, gap: 4, flex: 1}]}>
      <Text style={styles.pitcherName}>{st?.player_name || 'TBD'}</Text>
      <Text style={styles.pitcherStats}>{abbrev3(team)} QB</Text>
      {proj?.proj_fantasy_pts != null && (
        <Text style={styles.pitcherStats}>
          Proj FP: <Text style={styles.pitcherStatBold}>{Number(proj.proj_fantasy_pts).toFixed(1)}</Text>
        </Text>
      )}
      {proj?.proj_pass_yards != null && (
        <Text style={styles.pitcherStats}>
          Pass Y: <Text style={styles.pitcherStatBold}>{Math.round(Number(proj.proj_pass_yards))}</Text>
          {proj?.proj_pass_tds != null && ` · TD ${Number(proj.proj_pass_tds).toFixed(1)}`}
        </Text>
      )}
    </View>
  );

  return (
    <Section title="QB Matchup" hint="starters + weekly projections">
      <View style={{flexDirection: 'row', gap: 8}}>
        {renderQB('away', starters.away, projections.away, awayTeam)}
        {renderQB('home', starters.home, projections.home, homeTeam)}
      </View>
    </Section>
  );
}

// ─── NFL TEAM MATCHUP ────────────────────────────────────────────────────
// 2026-08-31 rewrite: casual-first layout matching NCAAFTeamMatchupCard.
// Reads server-computed summary JSONB (ctx.home_team_stats_summary +
// away_team_stats_summary) populated by nfl_game_context._build_team_summary.
// Falls back to raw nfl_team_stats fetch when summary blob is null (e.g.
// row hasn't been rebuilt post-migration yet). Ranks (1-based, lower =
// better) render as small gray chips next to each number.
function NFLTeamMatchupCard({ctx, homeTeam, awayTeam}: any) {
  const [fallback, setFallback] = useState<{home?: any; away?: any} | null>(null);
  const hasSummary = ctx?.home_team_stats_summary || ctx?.away_team_stats_summary;
  React.useEffect(() => {
    if (hasSummary) return;
    const client = sb();
    if (!client || !homeTeam || !awayTeam) return;
    (async () => {
      const season = ctx?.season || new Date().getFullYear();
      const {data: ts} = await client
        .from('nfl_team_stats')
        .select('team,pass_yards,rush_yards,def_sacks,def_ints,def_fumbles_forced,pass_tds,rush_tds,fg_made,sacks_suffered,games,season')
        .in('team', [homeTeam, awayTeam])
        .lte('season', season)
        .eq('season_type', 'REG')
        .order('season', {ascending: false})
        .limit(6);
      if (ts) {
        const map: any = {};
        for (const row of ts) if (!map[row.team]) map[row.team] = row;
        setFallback({home: map[homeTeam], away: map[awayTeam]});
      }
    })();
  }, [homeTeam, awayTeam, ctx?.season, hasSummary]);

  // Compose a normalized {home, away} summary — from server blob first, else fallback fetch.
  const home = ctx?.home_team_stats_summary || (fallback?.home ? _deriveNflSummary(fallback.home, ctx) : null);
  const away = ctx?.away_team_stats_summary || (fallback?.away ? _deriveNflSummary(fallback.away, ctx) : null);
  if (!home && !away) return null;

  // Fallback rows lack ctx defense fields → grab from ctx directly for the
  // "yds allowed" rows so the render matches server-blob path.
  const passAllH = home?.pass_yds_allowed_pg ?? ctx?.home_def_pass_ypg;
  const passAllA = away?.pass_yds_allowed_pg ?? ctx?.away_def_pass_ypg;
  const rushAllH = home?.rush_yds_allowed_pg ?? ctx?.home_def_rush_ypg;
  const rushAllA = away?.rush_yds_allowed_pg ?? ctx?.away_def_rush_ypg;
  const ptsAllH  = home?.pts_allowed_pg      ?? ctx?.home_def_ppg;
  const ptsAllA  = away?.pts_allowed_pg      ?? ctx?.away_def_ppg;

  const seasonUsed = home?.season_source ?? away?.season_source ?? ctx?.season;

  return (
    <Section title="Team Matchup" hint={seasonUsed ? `${seasonUsed} season · lower rank = better` : 'season stats · lower rank = better'}>
      <View style={{backgroundColor: C.surface2, borderRadius: 10, padding: 12}}>
        <View style={{flexDirection: 'row', alignItems: 'center', paddingBottom: 8,
                      borderBottomWidth: 1, borderBottomColor: C.border + '55'}}>
          <View style={{flex: 1}}>
            <Text style={{color: C.away, fontSize: 13, fontWeight: '800'}} numberOfLines={1}>{awayTeam}</Text>
          </View>
          <Text style={{width: 96, color: C.textMuted, fontSize: 9, fontWeight: '700',
                        textAlign: 'center', letterSpacing: 0.5}}>METRIC</Text>
          <View style={{flex: 1}}>
            <Text style={{color: C.home, fontSize: 13, fontWeight: '800', textAlign: 'right'}} numberOfLines={1}>{homeTeam}</Text>
          </View>
        </View>

        <RankedStatRow label="POINTS/G"     a={away?.pts_pg}      b={home?.pts_pg}
                       aRank={away?.rank_scoring_off} bRank={home?.rank_scoring_off} higherIsBetter />
        <RankedStatRow label="PTS ALLOW"    a={ptsAllA}           b={ptsAllH}
                       aRank={away?.rank_scoring_def} bRank={home?.rank_scoring_def} />
        <RankedStatRow label="PASS YDS/G"   a={away?.pass_yds_pg} b={home?.pass_yds_pg}
                       aRank={away?.rank_pass_off}    bRank={home?.rank_pass_off}    higherIsBetter />
        <RankedStatRow label="PASS ALLOW"   a={passAllA}          b={passAllH}
                       aRank={away?.rank_pass_def}    bRank={home?.rank_pass_def} />
        <RankedStatRow label="RUSH YDS/G"   a={away?.rush_yds_pg} b={home?.rush_yds_pg}
                       aRank={away?.rank_rush_off}    bRank={home?.rank_rush_off}    higherIsBetter />
        <RankedStatRow label="RUSH ALLOW"   a={rushAllA}          b={rushAllH}
                       aRank={away?.rank_rush_def}    bRank={home?.rank_rush_def} />
        <RankedStatRow label="SACKS/G"      a={away?.sacks_pg}    b={home?.sacks_pg} higherIsBetter />
        <RankedStatRow label="TAKEAWAYS/G"  a={away?.turnovers_forced_pg} b={home?.turnovers_forced_pg} higherIsBetter />
      </View>
    </Section>
  );
}

// Derive summary shape from raw nfl_team_stats row when server blob missing.
// Mirrors _build_team_summary in nfl_game_context.py but without ranks.
function _deriveNflSummary(s: any, ctx: any) {
  if (!s) return null;
  const g = Number(s.games) || 0;
  const pg = (f: string) => (s[f] == null || !g) ? null : Math.round((Number(s[f]) / g) * 10) / 10;
  const totalTds = (Number(s.pass_tds) || 0) + (Number(s.rush_tds) || 0);
  const fgMade = Number(s.fg_made) || 0;
  return {
    pts_pg: g && (totalTds || fgMade) ? Math.round((totalTds * 6.9 + fgMade * 3) / g * 10) / 10 : null,
    pts_allowed_pg: null,   // caller fills from ctx
    pass_yds_pg: pg('pass_yards'),
    pass_yds_allowed_pg: null,
    rush_yds_pg: pg('rush_yards'),
    rush_yds_allowed_pg: null,
    sacks_pg: pg('def_sacks'),
    turnovers_forced_pg: Math.round(((pg('def_ints') || 0) + (pg('def_fumbles_forced') || 0)) * 10) / 10,
    season_source: s.season,
    games_sample: g,
  };
}

// Shared paired-stat row with optional rank chip (used by NFL + NCAAF matchup cards).
function RankedStatRow({label, a, b, aRank, bRank, higherIsBetter = false, fmt}: any) {
  if (a == null && b == null) return null;
  const fA = fmt ? fmt(a) : (a == null ? '—' : Number(a).toFixed(a >= 100 ? 0 : 1));
  const fB = fmt ? fmt(b) : (b == null ? '—' : Number(b).toFixed(b >= 100 ? 0 : 1));
  const aAdv = (a != null && b != null) && (higherIsBetter ? a > b : a < b);
  const bAdv = (a != null && b != null) && (higherIsBetter ? b > a : b < a);
  const rankChip = (r: any) => (r == null ? null :
    <Text style={{color: C.textMuted, fontSize: 10, fontWeight: '600'}}> #{r}</Text>);
  return (
    <View style={{flexDirection: 'row', alignItems: 'center', paddingVertical: 4}}>
      <View style={{flex: 1, flexDirection: 'row', alignItems: 'baseline'}}>
        <Text style={{fontSize: 13, fontWeight: aAdv ? '800' : '600',
                      color: aAdv ? C.away : C.textDim}}>{fA}</Text>
        {rankChip(aRank)}
      </View>
      <Text style={{width: 96, fontSize: 10, fontWeight: '800', color: C.textMuted,
                    textAlign: 'center', letterSpacing: 0.5}}>{label}</Text>
      <View style={{flex: 1, flexDirection: 'row', alignItems: 'baseline', justifyContent: 'flex-end'}}>
        {rankChip(bRank)}
        <Text style={{fontSize: 13, fontWeight: bAdv ? '800' : '600',
                      color: bAdv ? C.home : C.textDim, textAlign: 'right', marginLeft: 2}}>{fB}</Text>
      </View>
    </View>
  );
}

// ─── NFL INJURIES (existing, extracted into its own component) ──────────
function NFLInjuriesCard({ctx, homeTeam, awayTeam}: any) {
  const [injuries, setInjuries] = useState<{home: any[]; away: any[]}>({home: [], away: []});
  React.useEffect(() => {
    const client = sb();
    if (!client || !homeTeam || !awayTeam) return;
    (async () => {
      const {data: inj} = await client.from('nfl_injuries')
        .select('team,player_name,position,injury_status,body_part,practice_status')
        .in('team', [homeTeam, awayTeam])
        .in('injury_status', ['Out', 'Doubtful', 'Questionable'])
        .order('updated_at', {ascending: false})
        .limit(20);
      if (inj) {
        setInjuries({
          home: inj.filter((r: any) => r.team === homeTeam).slice(0, 4),
          away: inj.filter((r: any) => r.team === awayTeam).slice(0, 4),
        });
      }
    })();
  }, [homeTeam, awayTeam]);
  if (injuries.home.length + injuries.away.length === 0) return null;
  return (
    <Section title="Injuries" hint="Out / Doubtful / Questionable">
      <View style={{gap: 6}}>
        {injuries.away.length > 0 && (
          <View>
            <Text style={styles.injSideLabel}>{abbrev3(awayTeam)}</Text>
            {injuries.away.map((r: any, i: number) => (
              <Text key={i} style={styles.injRow}>
                <Text style={{color: injStatusColor(r.injury_status)}}>[{r.injury_status?.[0]}]</Text>{' '}
                {r.player_name} ({r.position}) — {r.body_part || 'undisclosed'}
              </Text>
            ))}
          </View>
        )}
        {injuries.home.length > 0 && (
          <View>
            <Text style={styles.injSideLabel}>{abbrev3(homeTeam)}</Text>
            {injuries.home.map((r: any, i: number) => (
              <Text key={i} style={styles.injRow}>
                <Text style={{color: injStatusColor(r.injury_status)}}>[{r.injury_status?.[0]}]</Text>{' '}
                {r.player_name} ({r.position}) — {r.body_part || 'undisclosed'}
              </Text>
            ))}
          </View>
        )}
      </View>
    </Section>
  );
}

// ─── NFL SITUATIONAL (chips row — divisional, rest gap, cohort tags) ────
// Weather chips REMOVED here; the shared SportWeatherCard renders them
// as a proper section higher up.
function NFLSituationalCard({ctx, homeTeam, awayTeam}: any) {
  const tags = ctx?.cohort_tags || [];
  const rest = {home: ctx?.home_rest, away: ctx?.away_rest};
  const roof = ctx?.roof;
  const div = ctx?.div_game;
  const restGap = (rest.home != null && rest.away != null && Math.abs(rest.home - rest.away) >= 3);
  const hasAny = div || roof || restGap || (tags && tags.length);
  if (!hasAny) return null;
  return (
    <Section title="Situational">
      <View style={{flexDirection: 'row', flexWrap: 'wrap', gap: 6}}>
        {div && <SitChip label="Divisional" />}
        {roof && <SitChip label={`Roof: ${roof}`} />}
        {restGap && (
          <SitChip label={`Rest gap: ${abbrev3(rest.home > rest.away ? homeTeam : awayTeam)} +${Math.abs(rest.home - rest.away)}d`} kind="info" />
        )}
        {Array.isArray(tags) && tags.map((t: string, i: number) => (
          <SitChip key={i} label={t.replace(/^nfl_/, '').replace(/_/g, ' ')} />
        ))}
      </View>
    </Section>
  );
}

function TeamStatRow({team, side, stats, defense}: any) {
  if (!stats) return <Text style={styles.emptyMuted}>{team}: season stats unavailable</Text>;
  const passYPA = stats.pass_yards && stats.pass_attempts ? (stats.pass_yards / stats.pass_attempts).toFixed(1) : '—';
  const rushYPC = stats.rush_yards && stats.rush_attempts ? (stats.rush_yards / stats.rush_attempts).toFixed(1) : '—';
  const passEPA_per = stats.pass_epa != null && stats.pass_attempts ? (stats.pass_epa / stats.pass_attempts).toFixed(3) : '—';
  const defSacks = defense?.def_sacks;
  const defInts = defense?.def_ints;
  const defPassDef = defense?.def_pass_def;
  return (
    <View style={[styles.pitcherCard, {borderTopColor: side === 'home' ? C.home : C.away, padding: 12, gap: 4}]}>
      <Text style={styles.pitcherName}>{team} <Text style={{color: C.textMuted, fontWeight: '500', fontSize: 10}}>({stats.season} season)</Text></Text>
      <Text style={styles.pitcherStats}>
        Pass: <Text style={styles.pitcherStatBold}>{passYPA} YPA</Text> · EPA/att {passEPA_per} · {stats.pass_tds || 0} TD/{stats.pass_ints || 0} INT
      </Text>
      <Text style={styles.pitcherStats}>
        Rush: <Text style={styles.pitcherStatBold}>{rushYPC} YPC</Text> · sacks taken {stats.sacks_suffered || 0}
      </Text>
      {defense && (
        <Text style={[styles.pitcherStats, {color: C.textMuted, fontStyle: 'italic'}]}>
          vs {defense.team} D: {defSacks || '—'} sacks · {defInts || '—'} INT · {defPassDef || '—'} passes def
        </Text>
      )}
    </View>
  );
}

function SitChip({label, kind = 'neutral'}: {label: string; kind?: 'ok'|'warn'|'info'|'neutral'}) {
  return (
    <View style={[styles.sitChip, chipStyleFor(kind)]}>
      <Text style={[styles.sitChipText, {color: chipTextColorFor(kind)}]}>{label}</Text>
    </View>
  );
}

function injStatusColor(status: string): string {
  if (status === 'Out') return C.fade;
  if (status === 'Doubtful') return C.warn;
  if (status === 'Questionable') return C.sharp;
  return C.textMuted;
}

// ─── NBA SLOT ────────────────────────────────────────────────────────────
// 2026-08-25 build (see nba_slot_mock artifact). Season starts Oct 22 so
// most cards render empty until then — each returns null on missing data.
//
// Backend controls what shows via ctx fields:
//   Rest/B2B:     home_rest_days / home_is_b2b / away_rest_days / away_is_b2b
//   Team snap:    home_off_rating / home_def_rating / home_net_rating / home_pace
//   Elo:          elo_home / elo_away
//   Injuries:     home_starters_out TEXT[] + home_injury_impact NUMERIC
//                 + nba_injuries table for full list
//   Tendencies:   nba_team_home_road_tendencies materialized view
function NBASlot({ctx, game}: any) {
  const homeTeam = ctx?.home_team || game?.home_team;
  const awayTeam = ctx?.away_team || game?.away_team;
  return (
    <>
      <NBATeamSnapshotCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NBARestB2BCard      ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NBAInjuriesCard     ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NBAFourFactorsCard  ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      {/* TeamTendenciesCard removed 2026-09-01 — see NCAAF slot note. */}
    </>
  );
}

// Team snapshot — net rating + pace + off/def rating side by side.
function NBATeamSnapshotCard({ctx, homeTeam, awayTeam}: any) {
  const h = {net: ctx?.home_net_rating, pace: ctx?.home_pace, off: ctx?.home_off_rating, def: ctx?.home_def_rating, elo: ctx?.elo_home};
  const a = {net: ctx?.away_net_rating, pace: ctx?.away_pace, off: ctx?.away_off_rating, def: ctx?.away_def_rating, elo: ctx?.elo_away};
  if (h.net == null && a.net == null && h.elo == null && a.elo == null) return null;
  const fmt = (v: any, digits = 1) => v == null ? '—' : Number(v).toFixed(digits);
  return (
    <Section title="Team Snapshot" hint="net rating + pace + Elo">
      <View style={{flexDirection: 'row', gap: 8}}>
        <View style={[styles.pitcherCard, {borderTopColor: C.away, padding: 12, gap: 3, flex: 1}]}>
          <Text style={styles.pitcherName}>{awayTeam}</Text>
          {a.net != null && <Text style={styles.pitcherStats}>Net: <Text style={styles.pitcherStatBold}>{fmt(a.net)}</Text></Text>}
          {a.pace != null && <Text style={styles.pitcherStats}>Pace: <Text style={styles.pitcherStatBold}>{fmt(a.pace)}</Text></Text>}
          {a.off != null && a.def != null && <Text style={styles.pitcherStats}>Off/Def: <Text style={styles.pitcherStatBold}>{fmt(a.off, 0)}/{fmt(a.def, 0)}</Text></Text>}
          {a.elo != null && <Text style={styles.pitcherStats}>Elo: <Text style={styles.pitcherStatBold}>{fmt(a.elo, 0)}</Text></Text>}
        </View>
        <View style={[styles.pitcherCard, {borderTopColor: C.home, padding: 12, gap: 3, flex: 1}]}>
          <Text style={styles.pitcherName}>{homeTeam}</Text>
          {h.net != null && <Text style={styles.pitcherStats}>Net: <Text style={styles.pitcherStatBold}>{fmt(h.net)}</Text></Text>}
          {h.pace != null && <Text style={styles.pitcherStats}>Pace: <Text style={styles.pitcherStatBold}>{fmt(h.pace)}</Text></Text>}
          {h.off != null && h.def != null && <Text style={styles.pitcherStats}>Off/Def: <Text style={styles.pitcherStatBold}>{fmt(h.off, 0)}/{fmt(h.def, 0)}</Text></Text>}
          {h.elo != null && <Text style={styles.pitcherStats}>Elo: <Text style={styles.pitcherStatBold}>{fmt(h.elo, 0)}</Text></Text>}
        </View>
      </View>
    </Section>
  );
}

// Rest days + back-to-back (huge NBA signal).
function NBARestB2BCard({ctx, homeTeam, awayTeam}: any) {
  const hRest = ctx?.home_rest_days;
  const aRest = ctx?.away_rest_days;
  const hB2B = ctx?.home_is_b2b;
  const aB2B = ctx?.away_is_b2b;
  if (hRest == null && aRest == null && !hB2B && !aB2B) return null;
  const row = (team: string, rest: any, b2b: boolean, color: string) => (
    <View style={{flex: 1, backgroundColor: C.border + '22', padding: 10, borderRadius: 8, borderLeftWidth: 3, borderLeftColor: color}}>
      <Text style={{color: C.textMuted, fontSize: 10, fontWeight: '700', letterSpacing: 0.5}}>{abbrev3(team)}</Text>
      <View style={{flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4}}>
        <Text style={{color: C.text, fontSize: 13, fontWeight: '700'}}>
          {rest != null ? `${rest} day${rest === 1 ? '' : 's'} rest` : '—'}
        </Text>
        {b2b && (
          <View style={{backgroundColor: C.fade + '33', paddingHorizontal: 5, paddingVertical: 1, borderRadius: 3}}>
            <Text style={{color: C.fade, fontSize: 9, fontWeight: '800', letterSpacing: 0.5}}>2ND OF B2B</Text>
          </View>
        )}
      </View>
    </View>
  );
  return (
    <Section title="Rest &amp; B2B" hint="big NBA signal">
      <View style={{flexDirection: 'row', gap: 8}}>
        {row(awayTeam, aRest, !!aB2B, C.away)}
        {row(homeTeam, hRest, !!hB2B, C.home)}
      </View>
    </Section>
  );
}

// Injuries + line-move impact when quantified by backend.
function NBAInjuriesCard({ctx, homeTeam, awayTeam}: any) {
  const [injuries, setInjuries] = useState<{home: any[]; away: any[]}>({home: [], away: []});
  React.useEffect(() => {
    const client = sb();
    if (!client || !homeTeam || !awayTeam) return;
    (async () => {
      // NBA injuries keyed by team_abbrev, not team name
      const homeAbbr = ctx?.home_abbrev; const awayAbbr = ctx?.away_abbrev;
      const abbrs = [homeAbbr, awayAbbr].filter(Boolean);
      if (abbrs.length === 0) return;
      const {data} = await client.from('nba_injuries')
        .select('team_abbrev,player_name,status,reason')
        .in('team_abbrev', abbrs)
        .in('status', ['OUT', 'DOUBTFUL', 'QUESTIONABLE', 'GTD'])
        .order('updated_at', {ascending: false})
        .limit(20);
      if (data) {
        setInjuries({
          home: data.filter((r: any) => r.team_abbrev === homeAbbr).slice(0, 4),
          away: data.filter((r: any) => r.team_abbrev === awayAbbr).slice(0, 4),
        });
      }
    })();
  }, [homeTeam, awayTeam, ctx?.home_abbrev, ctx?.away_abbrev]);
  const impact = ctx?.home_injury_impact;
  const startersOut = ctx?.home_starters_out;
  if (injuries.home.length + injuries.away.length === 0 && !impact && !startersOut) return null;
  const renderSide = (label: string, rows: any[]) => rows.length === 0 ? null : (
    <View>
      <Text style={styles.injSideLabel}>{label}</Text>
      {rows.map((r: any, i: number) => (
        <Text key={i} style={styles.injRow}>
          <Text style={{color: injStatusColor(r.status?.charAt(0) + r.status?.slice(1).toLowerCase())}}>[{r.status?.charAt(0)}]</Text>{' '}
          {r.player_name} — {r.reason || 'undisclosed'}
        </Text>
      ))}
    </View>
  );
  return (
    <Section title="Injuries" hint="OUT / DOUBTFUL / QUESTIONABLE">
      <View style={{gap: 6}}>
        {renderSide(abbrev3(awayTeam), injuries.away)}
        {renderSide(abbrev3(homeTeam), injuries.home)}
        {impact != null && Math.abs(Number(impact)) >= 0.2 && (
          <Text style={{color: C.fade, fontSize: 11, fontStyle: 'italic', marginTop: 4}}>
            Starter-out impact score: {(Number(impact) * 100).toFixed(0)}% of typical starter value — line already reflects.
          </Text>
        )}
      </View>
    </Section>
  );
}

// Four Factors — eFG / TOV / ORB / FT for both teams from nba_team_stats.
function NBAFourFactorsCard({ctx, homeTeam, awayTeam}: any) {
  const [stats, setStats] = useState<{home?: any; away?: any}>({});
  React.useEffect(() => {
    const client = sb();
    if (!client) return;
    const homeAbbr = ctx?.home_abbrev; const awayAbbr = ctx?.away_abbrev;
    if (!homeAbbr && !awayAbbr) return;
    (async () => {
      const {data} = await client.from('nba_team_stats')
        .select('team_abbrev,season,efg_pct,tov_pct,orb_pct,ft_rate,opp_efg_pct,opp_tov_pct')
        .in('team_abbrev', [homeAbbr, awayAbbr].filter(Boolean))
        .order('season', {ascending: false})
        .limit(6);
      if (data) {
        const map: any = {};
        for (const r of data) if (!map[r.team_abbrev]) map[r.team_abbrev] = r;
        setStats({home: map[homeAbbr], away: map[awayAbbr]});
      }
    })();
  }, [ctx?.home_abbrev, ctx?.away_abbrev]);
  if (!stats.home && !stats.away) return null;
  const pct = (v: any) => v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`;
  const num = (v: any) => v == null ? '—' : Number(v).toFixed(2);
  const factors = [
    {label: 'eFG%',  away: pct(stats.away?.efg_pct),  home: pct(stats.home?.efg_pct)},
    {label: 'TOV%',  away: pct(stats.away?.tov_pct),  home: pct(stats.home?.tov_pct)},
    {label: 'ORB%',  away: pct(stats.away?.orb_pct),  home: pct(stats.home?.orb_pct)},
    {label: 'FT Rate', away: num(stats.away?.ft_rate), home: num(stats.home?.ft_rate)},
  ];
  return (
    <Section title="Four Factors" hint="eFG · TOV · ORB · FT">
      <View style={{flexDirection: 'row', paddingBottom: 4, borderBottomWidth: 0.5, borderBottomColor: C.border}}>
        <Text style={{flex: 1.3, color: C.textMuted, fontSize: 9, fontWeight: '800'}}>FACTOR</Text>
        <Text style={{flex: 1, color: C.away, fontSize: 9, fontWeight: '800', textAlign: 'center'}}>{abbrev3(awayTeam)}</Text>
        <Text style={{flex: 1, color: C.home, fontSize: 9, fontWeight: '800', textAlign: 'center'}}>{abbrev3(homeTeam)}</Text>
      </View>
      {factors.map((f, i) => (
        <View key={i} style={{flexDirection: 'row', paddingVertical: 5}}>
          <Text style={{flex: 1.3, color: C.textDim, fontSize: 12}}>{f.label}</Text>
          <Text style={{flex: 1, color: C.text, fontSize: 12, fontWeight: '700', textAlign: 'center'}}>{f.away}</Text>
          <Text style={{flex: 1, color: C.text, fontSize: 12, fontWeight: '700', textAlign: 'center'}}>{f.home}</Text>
        </View>
      ))}
    </Section>
  );
}

// ─── NCAAB SLOT ──────────────────────────────────────────────────────────
// 2026-08-25 build (see ncaab_slot_mock artifact). Season starts Nov 3.
// Efficiency Panel is the NCAAB differentiator — reads home_adj_em /
// away_adj_em / adj_em_gap directly (blended panel from KenPom + Torvik
// + Haslam materialized by ncaab_efficiency_model.py, wired 8/25).
function NCAABSlot({ctx, game}: any) {
  const homeTeam = ctx?.home_team || game?.home_team;
  const awayTeam = ctx?.away_team || game?.away_team;
  return (
    <>
      <NCAABEfficiencyCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NCAABPaceCard       ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NCAABFourFactorsCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NCAABFormRestCard   ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
    </>
  );
}

function NCAABEfficiencyCard({ctx, homeTeam, awayTeam}: any) {
  const h = {em: ctx?.home_adj_em, oe: ctx?.home_adj_oe, de: ctx?.home_adj_de};
  const a = {em: ctx?.away_adj_em, oe: ctx?.away_adj_oe, de: ctx?.away_adj_de};
  if (h.em == null && a.em == null) return null;
  const projSpread = ctx?.projected_spread;
  const closeSpread = ctx?.close_spread;
  const fmt = (v: any, d = 1) => v == null ? '—' : Number(v).toFixed(d);
  return (
    <Section title="Efficiency Panel" hint="blended rating panel">
      <View style={{flexDirection: 'row', gap: 8}}>
        <View style={[styles.pitcherCard, {borderTopColor: C.away, padding: 12, gap: 3, flex: 1}]}>
          <Text style={styles.pitcherName}>{awayTeam}</Text>
          {a.em != null && <Text style={styles.pitcherStats}>Adj EM: <Text style={styles.pitcherStatBold}>{fmt(a.em)}</Text></Text>}
          {a.oe != null && a.de != null && <Text style={styles.pitcherStats}>Off/Def: <Text style={styles.pitcherStatBold}>{fmt(a.oe, 0)}/{fmt(a.de, 0)}</Text></Text>}
        </View>
        <View style={[styles.pitcherCard, {borderTopColor: C.home, padding: 12, gap: 3, flex: 1}]}>
          <Text style={styles.pitcherName}>{homeTeam}</Text>
          {h.em != null && <Text style={styles.pitcherStats}>Adj EM: <Text style={styles.pitcherStatBold}>{fmt(h.em)}</Text></Text>}
          {h.oe != null && h.de != null && <Text style={styles.pitcherStats}>Off/Def: <Text style={styles.pitcherStatBold}>{fmt(h.oe, 0)}/{fmt(h.de, 0)}</Text></Text>}
        </View>
      </View>
      {projSpread != null && closeSpread != null && (
        <View style={{padding: 10, backgroundColor: C.accent + '10', borderRadius: 8, borderWidth: 1, borderColor: C.accent + '40', marginTop: 8}}>
          <Text style={{color: C.accent, fontWeight: '800', fontSize: 11, letterSpacing: 0.5, marginBottom: 4}}>PANEL READ</Text>
          <Text style={{color: C.text, fontSize: 12}}>
            Panel projects spread at {Number(projSpread).toFixed(1)} vs market {Number(closeSpread).toFixed(1)}.
          </Text>
        </View>
      )}
    </Section>
  );
}

function NCAABPaceCard({ctx, homeTeam, awayTeam}: any) {
  const hTempo = ctx?.home_tempo;
  const aTempo = ctx?.away_tempo;
  const paceAvg = ctx?.pace_avg;
  const closeTotal = ctx?.close_total;
  const projTotal = ctx?.projected_total;
  if (hTempo == null && aTempo == null && paceAvg == null) return null;
  return (
    <Section title="Pace &amp; Tempo" hint="projected possessions">
      <View style={{flexDirection: 'row', justifyContent: 'space-around', paddingVertical: 8}}>
        {aTempo != null && (
          <View style={{alignItems: 'center'}}>
            <Text style={{color: C.away, fontSize: 10, fontWeight: '800', letterSpacing: 0.5}}>{abbrev3(awayTeam)}</Text>
            <Text style={{color: C.text, fontSize: 18, fontWeight: '800'}}>{Number(aTempo).toFixed(1)}</Text>
            <Text style={{color: C.textMuted, fontSize: 10}}>poss</Text>
          </View>
        )}
        {paceAvg != null && (
          <View style={{alignItems: 'center'}}>
            <Text style={{color: C.accent, fontSize: 10, fontWeight: '800', letterSpacing: 0.5}}>PROJ</Text>
            <Text style={{color: C.accent, fontSize: 18, fontWeight: '800'}}>{Number(paceAvg).toFixed(1)}</Text>
            <Text style={{color: C.textMuted, fontSize: 10}}>blend</Text>
          </View>
        )}
        {hTempo != null && (
          <View style={{alignItems: 'center'}}>
            <Text style={{color: C.home, fontSize: 10, fontWeight: '800', letterSpacing: 0.5}}>{abbrev3(homeTeam)}</Text>
            <Text style={{color: C.text, fontSize: 18, fontWeight: '800'}}>{Number(hTempo).toFixed(1)}</Text>
            <Text style={{color: C.textMuted, fontSize: 10}}>poss</Text>
          </View>
        )}
      </View>
      {projTotal != null && closeTotal != null && (
        <Text style={{color: C.textDim, fontSize: 11, marginTop: 6, textAlign: 'center'}}>
          Total projection <Text style={{color: C.text, fontWeight: '700'}}>{Number(projTotal).toFixed(1)}</Text> vs line {Number(closeTotal).toFixed(1)}
        </Text>
      )}
    </Section>
  );
}

function NCAABFourFactorsCard({ctx, homeTeam, awayTeam}: any) {
  const h = {efg: ctx?.home_efg_o, to: ctx?.home_to_o, or: ctx?.home_or_o, ftr: ctx?.home_ftr_o};
  const a = {efg: ctx?.away_efg_o, to: ctx?.away_to_o, or: ctx?.away_or_o, ftr: ctx?.away_ftr_o};
  if (h.efg == null && a.efg == null) return null;
  const pct = (v: any) => v == null ? '—' : `${Number(v).toFixed(1)}%`;
  const num = (v: any) => v == null ? '—' : Number(v).toFixed(2);
  const factors = [
    {label: 'eFG%', away: pct(a.efg), home: pct(h.efg)},
    {label: 'TO%',  away: pct(a.to),  home: pct(h.to)},
    {label: 'OR%',  away: pct(a.or),  home: pct(h.or)},
    {label: 'FTR',  away: num(a.ftr), home: num(h.ftr)},
  ];
  return (
    <Section title="Four Factors" hint="ordered by predictive weight">
      <View style={{flexDirection: 'row', paddingBottom: 4, borderBottomWidth: 0.5, borderBottomColor: C.border}}>
        <Text style={{flex: 1.3, color: C.textMuted, fontSize: 9, fontWeight: '800'}}>FACTOR</Text>
        <Text style={{flex: 1, color: C.away, fontSize: 9, fontWeight: '800', textAlign: 'center'}}>{abbrev3(awayTeam)}</Text>
        <Text style={{flex: 1, color: C.home, fontSize: 9, fontWeight: '800', textAlign: 'center'}}>{abbrev3(homeTeam)}</Text>
      </View>
      {factors.map((f, i) => (
        <View key={i} style={{flexDirection: 'row', paddingVertical: 5}}>
          <Text style={{flex: 1.3, color: C.textDim, fontSize: 12}}>{f.label}</Text>
          <Text style={{flex: 1, color: C.text, fontSize: 12, fontWeight: '700', textAlign: 'center'}}>{f.away}</Text>
          <Text style={{flex: 1, color: C.text, fontSize: 12, fontWeight: '700', textAlign: 'center'}}>{f.home}</Text>
        </View>
      ))}
    </Section>
  );
}

function NCAABFormRestCard({ctx, homeTeam, awayTeam}: any) {
  const h = {rec: ctx?.home_record, l10: ctx?.home_l10, rest: ctx?.home_days_rest};
  const a = {rec: ctx?.away_record, l10: ctx?.away_l10, rest: ctx?.away_days_rest};
  if (!h.rec && !a.rec && h.rest == null && a.rest == null) return null;
  const line = (team: string, x: any) => (
    <View style={{flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4}}>
      <Text style={{color: C.textDim, fontSize: 12}}>{abbrev3(team)}</Text>
      <Text style={{color: C.text, fontSize: 12, fontWeight: '700'}}>
        {x.rec || '—'} · L10 {x.l10 || '—'} · rest {x.rest != null ? `${x.rest}d` : '—'}
      </Text>
    </View>
  );
  return (
    <Section title="Form &amp; Rest">
      {a.rec != null || a.rest != null ? line(awayTeam, a) : null}
      {h.rec != null || h.rest != null ? line(homeTeam, h) : null}
    </Section>
  );
}

// ─── COHORTS PANEL ──────────────────────────────────────────────────────
function CohortsPanel({ctx}: any) {
  const cb = safeJSON(ctx?.signal_confluence_breakdown) || {};
  const items = Object.entries(cb).filter(([_, v]) => v === 'home' || v === 'away');
  if (items.length === 0) return <Text style={styles.emptyMuted}>No cohort signals fired.</Text>;
  return (
    <View style={styles.cohortsGrid}>
      {items.map(([name, side]: any, i) => (
        <View key={i} style={[
          styles.cohort,
          {borderLeftColor: side === 'home' ? C.home : C.away},
        ]}>
          <Text style={styles.cohortName}>{name}</Text>
          <Text style={[styles.cohortSide, {color: side === 'home' ? C.home : C.away}]}>
            {String(side).toUpperCase()}
          </Text>
        </View>
      ))}
    </View>
  );
}

// ─── GAME PROPS PANEL ────────────────────────────────────────────────────
function GamePropsPanel({props: propsList}: {props: any[]}) {
  if (!propsList || propsList.length === 0) {
    return <Text style={styles.emptyMuted}>No qualifying props for this game.</Text>;
  }
  return (
    <View style={{gap: 4}}>
      {propsList.slice(0, 8).map((p, i) => {
        const dir = String(p.direction || '').toLowerCase() === 'over' ? '↑' : '↓';
        const projected = p.projected_value ?? p.projected ?? null;
        return (
          <View key={i} style={styles.propRow}>
            <View style={[styles.propTier, tierPillStyle(p.tier)]}>
              <Text style={[styles.propTierText, {color: tierPillTextColor(p.tier)}]}>{p.tier}</Text>
            </View>
            <View style={{flex: 1}}>
              <Text style={styles.propPlayer} numberOfLines={1}>{p.player_name || '?'}</Text>
              <Text style={styles.propDetail}>
                Line <Text style={styles.propBold}>{p.prop_line}</Text> {String(p.prop_type || '')}
                {projected != null && <> · Projected <Text style={styles.propBold}>{f(projected, 1)}</Text></>}
              </Text>
            </View>
            <View style={{alignItems: 'flex-end'}}>
              <Text style={[styles.propDetail, {color: C.text, fontWeight: '700'}]}>{dir} {p.direction}</Text>
              <Text style={[styles.propDetail, {fontSize: 9}]}>conv {p.conviction}</Text>
            </View>
          </View>
        );
      })}
    </View>
  );
}

// ─── YOUR BOOK TILES (HRB) ───────────────────────────────────────────────
// Toggle-select behavior: tap a tile → it highlights as SELECTED. Then the
// Log Pick / Add to Parlay buttons at the bottom act on the selected tile.
// Default selected = primary_play if it maps to a tile, else no selection.
function YourBookTiles({
  closeSpread, closeTotal, homeML, awayML, homeTeam, awayTeam, primaryPlay,
  bookmakers = [], onAddParlayLeg, onLogPick,
}: any) {
  const hrb = (bookmakers || []).find((b: any) =>
    (b.key || '').toLowerCase().includes('hardrock') || (b.title || '').toLowerCase().includes('hard rock'),
  );
  const findMarket = (mk: string) => hrb?.markets?.find((m: any) => m.key === mk);
  const spreadMkt = findMarket('spreads');
  const totalMkt = findMarket('totals');
  const h2hMkt = findMarket('h2h');

  const homeSpreadOutcome = spreadMkt?.outcomes?.find((o: any) => o.name === homeTeam);
  const awaySpreadOutcome = spreadMkt?.outcomes?.find((o: any) => o.name === awayTeam);
  const overOutcome = totalMkt?.outcomes?.find((o: any) => (o.name || '').toLowerCase() === 'over');
  const underOutcome = totalMkt?.outcomes?.find((o: any) => (o.name || '').toLowerCase() === 'under');
  const homeMLOutcome = h2hMkt?.outcomes?.find((o: any) => o.name === homeTeam);
  const awayMLOutcome = h2hMkt?.outcomes?.find((o: any) => o.name === awayTeam);

  const spreadHomeLine = homeSpreadOutcome?.point ?? closeSpread;
  const spreadHomeOdds = homeSpreadOutcome?.price;
  const spreadAwayLine = awaySpreadOutcome?.point ?? (closeSpread != null ? -closeSpread : null);
  const spreadAwayOdds = awaySpreadOutcome?.price;
  const totalLine = overOutcome?.point ?? closeTotal;
  const overOdds = overOutcome?.price;
  const underOdds = underOutcome?.price;
  const finalHomeML = homeMLOutcome?.price ?? homeML;
  const finalAwayML = awayMLOutcome?.price ?? awayML;

  // Default-select the primary_play if it maps to one of our tiles
  const primaryDefault: any = (() => {
    if (!primaryPlay?.type) return null;
    if (primaryPlay.type === 'ml' && primaryPlay.label) {
      if (primaryPlay.label.includes(homeTeam)) return {key: 'ml_home'};
      if (primaryPlay.label.includes(awayTeam)) return {key: 'ml_away'};
    }
    if (primaryPlay.type === 'over') return {key: 'over'};
    if (primaryPlay.type === 'under') return {key: 'under'};
    return null;
  })();
  const [selectedKey, setSelectedKey] = useState<string | null>(primaryDefault?.key || null);

  // Tile definitions (single source of truth for selection + action wiring)
  const tiles: Record<string, {label: string; val: string; odds: any; line: any; pickLabel: string; type: string}> = {
    spread_home: {
      label: 'Spread H',
      val: spreadHomeLine != null ? `${abbrev3(homeTeam)} ${spreadHomeLine > 0 ? '+' : ''}${spreadHomeLine}` : '—',
      odds: spreadHomeOdds, line: spreadHomeLine,
      pickLabel: spreadHomeLine != null ? `${abbrev3(homeTeam)} ${spreadHomeLine > 0 ? '+' : ''}${spreadHomeLine}` : '—',
      type: 'RL',
    },
    spread_away: {
      label: 'Spread A',
      val: spreadAwayLine != null ? `${abbrev3(awayTeam)} ${spreadAwayLine > 0 ? '+' : ''}${spreadAwayLine}` : '—',
      odds: spreadAwayOdds, line: spreadAwayLine,
      pickLabel: spreadAwayLine != null ? `${abbrev3(awayTeam)} ${spreadAwayLine > 0 ? '+' : ''}${spreadAwayLine}` : '—',
      type: 'RL',
    },
    over: {
      label: 'Over',
      val: `O ${f(totalLine, 1)}`,
      odds: overOdds, line: totalLine,
      pickLabel: `Over ${f(totalLine, 1)}`,
      type: 'Total',
    },
    under: {
      label: 'Under',
      val: `U ${f(totalLine, 1)}`,
      odds: underOdds, line: totalLine,
      pickLabel: `Under ${f(totalLine, 1)}`,
      type: 'Total',
    },
    ml_away: {
      label: `${abbrev3(awayTeam)} ML`,
      val: fmtOdds(finalAwayML),
      odds: finalAwayML, line: null,
      pickLabel: `${abbrev3(awayTeam)} ML`,
      type: 'ML',
    },
    ml_home: {
      label: `${abbrev3(homeTeam)} ML`,
      val: fmtOdds(finalHomeML),
      odds: finalHomeML, line: null,
      pickLabel: `${abbrev3(homeTeam)} ML`,
      type: 'ML',
    },
  };

  const renderTile = (key: string) => {
    const t = tiles[key];
    if (!t) return null;
    const isSel = selectedKey === key;
    const isPrimary = primaryDefault?.key === key;
    return (
      <TouchableOpacity
        key={key}
        style={[
          styles.hrbTile,
          isSel && {borderColor: C.accent, backgroundColor: C.accentDim, borderWidth: 2},
        ]}
        onPress={() => setSelectedKey(selectedKey === key ? null : key)}
        activeOpacity={0.7}
      >
        <Text style={[styles.hrbTileLabel, isSel && {color: C.accent}]}>
          {t.label}{isPrimary ? ' ★' : ''}
        </Text>
        <Text style={[styles.hrbTileVal, isSel && {color: C.accent}]}>{t.val}</Text>
        {(key === 'spread_home' || key === 'spread_away' || key === 'over' || key === 'under') && (
          <Text style={[styles.hrbTileOdds, isSel && {color: C.accent}]}>{fmtOdds(t.odds)}</Text>
        )}
      </TouchableOpacity>
    );
  };

  const selected = selectedKey ? tiles[selectedKey] : null;
  const canAct = !!selected && selected.val !== '—';

  const doAddParlay = () => {
    if (!selected) return;
    onAddParlayLeg?.({
      kind: selectedKey,
      label: selected.pickLabel,
      odds: selected.odds,
      line: selected.line,
      matchup: `${awayTeam} @ ${homeTeam}`,
      book: 'Hard Rock Bet',
    });
  };
  const doLogPick = () => {
    if (!selected) return;
    onLogPick?.({
      pick: selected.pickLabel,
      type: selected.type,
      odds: selected.odds,
      matchup: `${awayTeam} @ ${homeTeam}`,
      book: 'Hard Rock Bet',
    });
  };

  return (
    <View>
      {/* Row 1: spread home + total over + ML home */}
      <View style={styles.hrbTiles}>
        {renderTile('spread_home')}
        {renderTile('over')}
        {renderTile('ml_home')}
      </View>
      {/* Row 2: spread away + total under + ML away */}
      <View style={[styles.hrbTiles, {marginTop: 6}]}>
        {renderTile('spread_away')}
        {renderTile('under')}
        {renderTile('ml_away')}
      </View>

      {/* Selection status + Action buttons */}
      <Text style={styles.hrbSelectionHint}>
        {selected
          ? <>Selected: <Text style={{color: C.accent, fontWeight: '700'}}>{selected.pickLabel}</Text> {selected.odds != null ? `@ ${fmtOdds(selected.odds)}` : ''}</>
          : 'Tap a tile above to select a pick'}
      </Text>

      <View style={{flexDirection: 'row', gap: 8, marginTop: 10}}>
        <TouchableOpacity
          style={[styles.parlayCta, {flex: 1, opacity: canAct ? 1 : 0.4}]}
          disabled={!canAct}
          onPress={doAddParlay}
          activeOpacity={0.7}
        >
          <Text style={styles.parlayCtaText}>+ Parlay</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.parlayCta, {
            flex: 1,
            backgroundColor: 'transparent',
            borderWidth: 1,
            borderColor: C.accent,
            opacity: canAct ? 1 : 0.4,
          }]}
          disabled={!canAct}
          onPress={doLogPick}
          activeOpacity={0.7}
        >
          <Text style={[styles.parlayCtaText, {color: C.accent}]}>Log Pick</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

// ─── ALL BOOK LINES (real table, tap-to-add-leg) ─────────────────────────
function AllBookLinesPanel({bookmakers, homeTeam, awayTeam, onAddParlayLeg}: any) {
  if (!bookmakers || bookmakers.length === 0) {
    return <Text style={styles.emptyMuted}>No book lines available.</Text>;
  }
  // Sort HRB first (pinned), rest alphabetical
  const sorted = [...bookmakers].sort((a: any, b: any) => {
    const aHRB = /hardrock|hard rock/i.test(a.key || a.title || '');
    const bHRB = /hardrock|hard rock/i.test(b.key || b.title || '');
    if (aHRB && !bHRB) return -1;
    if (bHRB && !aHRB) return 1;
    return (a.title || '').localeCompare(b.title || '');
  });

  const addLeg = (kind: string, label: string, odds: any, line: any, bookTitle: string) => {
    onAddParlayLeg?.({kind, label, odds, line, matchup: `${awayTeam} @ ${homeTeam}`, book: bookTitle});
  };

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{paddingRight: 12}}>
      <View style={{minWidth: 320}}>
        {/* Header row */}
        <View style={styles.bookTableHeader}>
          <Text style={[styles.bookTh, {flex: 1.6}]}>Book</Text>
          <Text style={[styles.bookTh, {flex: 1.2, textAlign: 'right'}]}>Spread</Text>
          <Text style={[styles.bookTh, {flex: 1, textAlign: 'right'}]}>Total</Text>
          <Text style={[styles.bookTh, {flex: 1, textAlign: 'right'}]}>ML A</Text>
          <Text style={[styles.bookTh, {flex: 1, textAlign: 'right'}]}>ML H</Text>
        </View>
        {sorted.map((bm: any, i: number) => {
          const spreadMkt = bm.markets?.find((m: any) => m.key === 'spreads');
          const totalMkt = bm.markets?.find((m: any) => m.key === 'totals');
          const h2hMkt = bm.markets?.find((m: any) => m.key === 'h2h');
          const homeSpread = spreadMkt?.outcomes?.find((o: any) => o.name === homeTeam);
          const overTot = totalMkt?.outcomes?.find((o: any) => (o.name || '').toLowerCase() === 'over');
          const awayML = h2hMkt?.outcomes?.find((o: any) => o.name === awayTeam);
          const homeML = h2hMkt?.outcomes?.find((o: any) => o.name === homeTeam);
          const isHRB = /hardrock|hard rock/i.test(bm.key || bm.title || '');
          return (
            <View key={i} style={[styles.bookTableRow, isHRB && {backgroundColor: C.accentDim}]}>
              <Text style={[styles.bookTd, {flex: 1.6, fontWeight: isHRB ? '700' : '400'}]} numberOfLines={1}>
                {isHRB ? '★ ' : ''}{bm.title || bm.key}
              </Text>
              <TouchableOpacity
                style={{flex: 1.2}}
                onPress={() => homeSpread && addLeg('spread', `${abbrev3(homeTeam)} ${homeSpread.point > 0 ? '+' : ''}${homeSpread.point}`, homeSpread.price, homeSpread.point, bm.title)}
                activeOpacity={0.6}
              >
                <Text style={[styles.bookTd, {textAlign: 'right'}]}>
                  {homeSpread ? `${homeSpread.point > 0 ? '+' : ''}${homeSpread.point}` : '—'}
                  {homeSpread?.price ? ` (${fmtOdds(homeSpread.price)})` : ''}
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={{flex: 1}}
                onPress={() => overTot && addLeg('total', `O ${overTot.point}`, overTot.price, overTot.point, bm.title)}
                activeOpacity={0.6}
              >
                <Text style={[styles.bookTd, {textAlign: 'right'}]}>
                  {overTot ? `O${overTot.point}` : '—'}
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={{flex: 1}}
                onPress={() => awayML && addLeg('ml', `${abbrev3(awayTeam)} ML`, awayML.price, null, bm.title)}
                activeOpacity={0.6}
              >
                <Text style={[styles.bookTd, {textAlign: 'right'}]}>{fmtOdds(awayML?.price)}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={{flex: 1}}
                onPress={() => homeML && addLeg('ml', `${abbrev3(homeTeam)} ML`, homeML.price, null, bm.title)}
                activeOpacity={0.6}
              >
                <Text style={[styles.bookTd, {textAlign: 'right'}]}>{fmtOdds(homeML?.price)}</Text>
              </TouchableOpacity>
            </View>
          );
        })}
      </View>
    </ScrollView>
  );
}

// ─── NUMBERS PANEL ──────────────────────────────────────────────────────
function NumbersPanel({ctx, awayTeam, homeTeam, sport}: any) {
  const mc = safeJSON(ctx?.mc_probabilities) || {};
  const rows = [
    ['Panel', ctx?.panel_implied_margin, ctx?.panel_implied_total,
      p2(ctx?.panel_implied_total, ctx?.panel_implied_margin, 'a'),
      p2(ctx?.panel_implied_total, ctx?.panel_implied_margin, 'h')],
    ['Jerry', ctx?.jerry_pred_spread, ctx?.jerry_pred_total,
      p2(ctx?.jerry_pred_total, ctx?.jerry_pred_spread, 'a'),
      p2(ctx?.jerry_pred_total, ctx?.jerry_pred_spread, 'h')],
    ['v3', ctx?.projected_spread, ctx?.projected_total, null, null],
    ['v4', ctx?.model_pred_spread, ctx?.model_pred_total, null, null],
    ['MC', mc.mc_expected_margin, mc.mc_expected_total ?? mc.mc_mean_total, null, null],
  ];
  return (
    <View style={{gap: 12}}>
      <Text style={styles.numbersHeading}>Per-Model Predictions</Text>
      <View style={styles.numbersTable}>
        <View style={styles.numbersTableRow}>
          <Text style={[styles.numbersTh, {flex: 1.4}]}>Model</Text>
          <Text style={[styles.numbersTh, {flex: 1, textAlign: 'right'}]}>Margin</Text>
          <Text style={[styles.numbersTh, {flex: 1, textAlign: 'right'}]}>Total</Text>
          <Text style={[styles.numbersTh, {flex: 1, textAlign: 'right'}]}>{abbrev3(awayTeam)}</Text>
          <Text style={[styles.numbersTh, {flex: 1, textAlign: 'right'}]}>{abbrev3(homeTeam)}</Text>
        </View>
        {rows.map((r: any, i) => (
          <View key={i} style={styles.numbersTableRow}>
            <Text style={[styles.numbersTd, {flex: 1.4}]}>{r[0]}</Text>
            <Text style={[styles.numbersTd, {flex: 1, textAlign: 'right'}]}>{f(r[1], 2)}</Text>
            <Text style={[styles.numbersTd, {flex: 1, textAlign: 'right'}]}>{f(r[2], 2)}</Text>
            <Text style={[styles.numbersTd, {flex: 1, textAlign: 'right'}]}>{f(r[3], 2)}</Text>
            <Text style={[styles.numbersTd, {flex: 1, textAlign: 'right'}]}>{f(r[4], 2)}</Text>
          </View>
        ))}
      </View>

      <Text style={styles.numbersHeading}>MC Probabilities (10k sims)</Text>
      <View style={styles.numbersMCGrid}>
        <MCTile label="Home win prob" value={mc.mc_home_win_prob != null ? `${(mc.mc_home_win_prob * 100).toFixed(1)}%` : '—'} />
        <MCTile label="Away win prob" value={mc.mc_away_win_prob != null ? `${(mc.mc_away_win_prob * 100).toFixed(1)}%` : '—'} />
        <MCTile label="Over prob" value={mc.mc_p_over != null ? `${(mc.mc_p_over * 100).toFixed(1)}%` : '—'} />
        <MCTile label="Under prob" value={mc.mc_p_under != null ? `${(mc.mc_p_under * 100).toFixed(1)}%` : '—'} />
        <MCTile label="Mean total" value={mc.mc_mean_total != null ? f(mc.mc_mean_total, 2) : '—'} />
        <MCTile label="Std total" value={mc.mc_std_total != null ? f(mc.mc_std_total, 2) : '—'} />
        {/* 2026-08-09: NRFI/YRFI are MLB-only concepts (No Runs First Inning);
            hide the tiles for other sports where mc_p_nrfi never populates. */}
        {sport === 'MLB' && <MCTile label="NRFI prob" value={mc.mc_p_nrfi != null ? `${(mc.mc_p_nrfi * 100).toFixed(1)}%` : '—'} />}
        {sport === 'MLB' && <MCTile label="YRFI prob" value={mc.mc_p_yrfi != null ? `${(mc.mc_p_yrfi * 100).toFixed(1)}%` : '—'} />}
      </View>

      {ctx?.signal_confluence_v2_breakdown && (
        <>
          <Text style={styles.numbersHeading}>v2 Cohorts (shadow)</Text>
          <Text style={styles.numbersMono}>
            v2_net = <Text style={{color: (ctx.signal_confluence_v2_net || 0) > 0 ? C.home : C.away, fontWeight: '700'}}>
              {(ctx.signal_confluence_v2_net || 0) > 0 ? '+' : ''}{ctx.signal_confluence_v2_net || 0}
            </Text>
          </Text>
        </>
      )}
    </View>
  );
}

function MCTile({label, value}: any) {
  return (
    <View style={styles.mcTile}>
      <Text style={styles.mcTileLabel}>{label}</Text>
      <Text style={styles.mcTileValue}>{value}</Text>
    </View>
  );
}

// ─── Helpers ────────────────────────────────────────────────────────────
function safeJSON(v: any) {
  if (!v) return null;
  if (typeof v === 'object') return v;
  try { return JSON.parse(v); } catch { return null; }
}

function p2(tot: any, mgn: any, which: 'a'|'h'): number | null {
  if (tot == null || mgn == null) return null;
  const t = parseFloat(tot); const m = parseFloat(mgn);
  if (!isFinite(t) || !isFinite(m)) return null;
  return which === 'a' ? (t - m) / 2 : (t + m) / 2;
}

function tierBadgeStyle(tier: string) {
  const t = String(tier).toUpperCase();
  if (t === 'PRIME') return {backgroundColor: C.accentDim, borderColor: C.accent, color: C.accent};
  if (t === 'STRONG') return {backgroundColor: C.sharpDim, borderColor: C.sharp, color: C.sharp};
  if (t === 'LEAN') return {backgroundColor: C.warnDim, borderColor: C.warn, color: C.warn};
  if (t === 'LIGHT') return {backgroundColor: C.surface2, borderColor: C.border, color: C.textMuted};
  return {backgroundColor: C.surface2, borderColor: C.border, color: C.textMuted};
}

function tierPillStyle(tier: string) {
  const t = String(tier).toUpperCase();
  if (t === 'PRIME') return {backgroundColor: C.accentDim};
  if (t === 'STRONG') return {backgroundColor: C.sharpDim};
  if (t === 'LEAN') return {backgroundColor: C.warnDim};
  return {backgroundColor: C.surface2};
}

function tierPillTextColor(tier: string) {
  const t = String(tier).toUpperCase();
  if (t === 'PRIME') return C.accent;
  if (t === 'STRONG') return C.sharp;
  if (t === 'LEAN') return C.warn;
  return C.textMuted;
}

function chipStyleFor(kind: 'ok'|'warn'|'info'|'neutral') {
  if (kind === 'ok') return {backgroundColor: C.accentDim, borderColor: C.accent};
  if (kind === 'warn') return {backgroundColor: C.warnDim, borderColor: C.warn};
  if (kind === 'info') return {backgroundColor: C.sharpDim, borderColor: C.sharp};
  return {backgroundColor: C.surface, borderColor: C.border};
}

function chipTextColorFor(kind: 'ok'|'warn'|'info'|'neutral') {
  if (kind === 'ok') return C.accent;
  if (kind === 'warn') return C.warn;
  if (kind === 'info') return C.sharp;
  return C.text;
}

function cohortBadge(ctx: any): string {
  const cb = safeJSON(ctx?.signal_confluence_breakdown);
  if (!cb) return 'no data';
  const fired = Object.entries(cb).filter(([_, v]) => v === 'home' || v === 'away').length;
  const net = ctx?.signal_confluence_net;
  return `${fired} fired · net ${net != null && net > 0 ? '+' : ''}${net ?? '—'}`;
}

// 2026-08-23: Public splits panel — reads game_context.splits_summary JSONB
// populated by splits_v2_pipeline. Shows per-market source badges + confirmed
// markers. Gating (2026-09-01 per user directive): sports where only 1 or
// 2 external money-flow sources exist (NCAAF has CZ+SO, NHL has SO only)
// should NOT be advertised as "triple confirmed" — that's a lie. Badge and
// per-side chip adapt to the actual sources_present count.
//   0 sources → don't render (gated upstream at ctx.splits_summary presence)
//   1 source  → "1 source · unconfirmed"        · no side chip
//   2 sources → "2 sources · confirmed"         · DOUBLE chip when 2 agree
//   3+ srcs   → "3 sources · triple-confirmed"  · TRIPLE chip when 3+ agree
function splitsBadge(summary: any): string {
  const srcs = Array.isArray(summary?.sources_present) ? summary.sources_present : [];
  const triple = Array.isArray(summary?.triple_confirmed) ? summary.triple_confirmed : [];
  if (srcs.length === 0) return 'no sources';
  if (srcs.length === 1) return '1 source · unconfirmed';
  if (srcs.length === 2) {
    // With 2 sources max, "triple_confirmed" can't fire per the aggregator's
    // ≥3 rule. But some sides may have both sources agreeing → "confirmed".
    const doubles = _doubleConfirmedFromSummary(summary);
    return doubles.length
      ? `2 sources · ${doubles.length} confirmed`
      : '2 sources · dissenting';
  }
  return `${srcs.length} sources${triple.length ? ` · ${triple.length} triple-confirmed` : ''}`;
}
function _doubleConfirmedFromSummary(summary: any): string[] {
  const doubles: string[] = [];
  const MARKETS = ['ml', 'rl', 'spread', 'total', 'moneyline'];
  for (const mkt of MARKETS) {
    const mData = summary?.[mkt];
    if (!mData || typeof mData !== 'object') continue;
    for (const [side, agg] of Object.entries<any>(mData)) {
      if (agg?.sources_agree >= 2) doubles.push(`${mkt}_${side}`);
    }
  }
  return doubles;
}

function SplitsSummaryPanel({summary}: any) {
  const s = summary || {};
  const srcs: string[] = Array.isArray(s.sources_present) ? s.sources_present : [];
  const triple: string[] = Array.isArray(s.triple_confirmed) ? s.triple_confirmed : [];
  const MARKETS = ['ml', 'rl', 'total'];
  const marketLabel: Record<string, string> = {ml: 'Moneyline', rl: 'Run/Puck Line', total: 'Total'};
  // 2026-08-25: anonymized labels — same "Split N" convention as
  // LineMovementTab / feedback_tos_scrub_source_names. Never leak
  // vendor names ('OddsCrowd', 'Fadereport', etc) to user copy.
  const sourceLabel: Record<string, string> = {oc: 'Split 1', fr: 'Split 2', cz: 'Split 3', so: 'Split 4'};
  return (
    <View style={{gap: 10}}>
      {/* Sources present row */}
      {srcs.length > 0 && (
        <View style={{flexDirection: 'row', flexWrap: 'wrap', gap: 6, alignItems: 'center'}}>
          <Text style={{color: C.textMuted, fontSize: 10, fontWeight: '700', marginRight: 4}}>
            SOURCES:
          </Text>
          {srcs.map((src, i) => (
            <View key={i} style={{
              backgroundColor: C.accent + '18', borderColor: C.accent + '55', borderWidth: 1,
              borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3,
            }}>
              <Text style={{color: C.accent, fontSize: 10, fontWeight: '700'}}>
                {sourceLabel[src] || String(src).toUpperCase()}
              </Text>
            </View>
          ))}
        </View>
      )}

      {/* Per-market breakdown */}
      {MARKETS.map(mkt => {
        const mkt_data = s[mkt];
        if (!mkt_data || typeof mkt_data !== 'object') return null;
        const sides = Object.entries(mkt_data);
        if (sides.length === 0) return null;
        return (
          <View key={mkt} style={{gap: 4}}>
            <Text style={{color: C.text, fontSize: 11, fontWeight: '700'}}>
              {marketLabel[mkt] || String(mkt).toUpperCase()}
            </Text>
            {sides.map(([side, agg]: any, i) => {
              const money = agg?.money_pct_avg;
              const bets = agg?.bets_pct_avg;
              const nSrc = agg?.sources_agree ?? 0;
              // 2026-09-01: adaptive confirmation chip. Was TRIPLE-only which
              // lied for NCAAF/NCAAB/NHL where max sources ≤ 2. Now:
              //   nSrc >= 3 → TRIPLE (cyan/sharp)
              //   nSrc == 2 → DOUBLE (cyan-dim)
              //   nSrc == 1 → nothing (unconfirmed, single-source)
              const isTriple = nSrc >= 3;
              const isDouble = nSrc === 2;
              const confirmed = isTriple || isDouble;
              return (
                <View key={i} style={{
                  flexDirection: 'row', alignItems: 'center', gap: 8,
                  paddingHorizontal: 8, paddingVertical: 4,
                  backgroundColor: confirmed ? (C.sharp + '15') : 'transparent',
                  borderRadius: 6,
                }}>
                  <Text style={{color: C.textDim, fontSize: 11, minWidth: 50, fontWeight: '600'}}>
                    {String(side).toUpperCase()}
                  </Text>
                  {money != null && (
                    <Text style={{color: C.text, fontSize: 11}}>
                      Money <Text style={{fontWeight: '700'}}>{money}%</Text>
                    </Text>
                  )}
                  {bets != null && (
                    <Text style={{color: C.textDim, fontSize: 11}}>
                      Bets <Text style={{fontWeight: '700'}}>{bets}%</Text>
                    </Text>
                  )}
                  <Text style={{color: C.textMuted, fontSize: 9}}>
                    {nSrc} src{nSrc === 1 ? '' : 's'}
                  </Text>
                  {isTriple && (
                    <Text style={{color: C.sharp, fontSize: 9, fontWeight: '800'}}>
                      TRIPLE
                    </Text>
                  )}
                  {isDouble && (
                    <Text style={{color: C.sharp, fontSize: 9, fontWeight: '700', opacity: 0.75}}>
                      DOUBLE
                    </Text>
                  )}
                </View>
              );
            })}
          </View>
        );
      })}

      {srcs.length === 0 && (
        <Text style={styles.emptyMuted}>No public splits data for this game yet.</Text>
      )}
    </View>
  );
}

// ─── STYLES ─────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  root: {flex: 1, backgroundColor: C.bg},

  // Header
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    paddingHorizontal: 18,
    paddingTop: 14,
    paddingBottom: 12,
    backgroundColor: C.surface,
    borderBottomWidth: 1,
    borderBottomColor: C.border,
    gap: 12,
  },
  hdrMatchup: {fontSize: 15, fontWeight: '700', letterSpacing: -0.2, lineHeight: 20},
  hdrMeta: {fontSize: 11, color: C.textMuted, marginTop: 3, fontVariant: ['tabular-nums']},
  closeBtn: {
    width: 28, height: 28, borderRadius: 14, backgroundColor: C.surface2,
    alignItems: 'center', justifyContent: 'center',
  },
  closeBtnText: {color: C.textMuted, fontSize: 15},

  // Verdict
  verdict: {
    paddingHorizontal: 18, paddingTop: 20, paddingBottom: 18,
    backgroundColor: C.accentBg,
    borderBottomWidth: 1, borderBottomColor: C.border,
  },
  verdictTierPill: {
    alignSelf: 'flex-start',
    paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: 4, borderWidth: 1,
    marginBottom: 8,
  },
  verdictTierText: {fontSize: 10, fontWeight: '800', letterSpacing: 1.2},
  verdictPlay: {fontSize: 22, fontWeight: '700', color: C.text, letterSpacing: -0.4, lineHeight: 26, marginBottom: 4},
  verdictWhy: {fontSize: 13, color: C.textMuted, lineHeight: 19, marginTop: 6},
  verdictNoPlay: {fontSize: 12, color: C.textMuted, fontStyle: 'italic'},

  // Losing-market context chips (signals that fired on a market's losing side)
  losingChipsWrap: {
    paddingTop: 6, paddingBottom: 10,
    borderBottomWidth: 1, borderBottomColor: C.border,
    backgroundColor: C.surface2,
  },
  losingChipsHint: {
    fontSize: 10, fontWeight: '700', letterSpacing: 1.2,
    color: C.textMuted, paddingHorizontal: 18, marginBottom: 6,
  },
  losingChip: {
    backgroundColor: C.surface,
    borderWidth: 1, borderColor: C.border,
    borderRadius: 8,
    paddingHorizontal: 10, paddingVertical: 6,
    maxWidth: 220,
  },
  losingChipMarket: {
    fontSize: 9, fontWeight: '800', letterSpacing: 1,
    color: C.textMuted, marginBottom: 2,
  },
  losingChipProse: {
    fontSize: 12, color: C.textDim, lineHeight: 15,
  },

  // Jerry read
  jerrySection: {
    paddingHorizontal: 18, paddingTop: 14, paddingBottom: 16,
    backgroundColor: C.surface2,
    borderBottomWidth: 1, borderBottomColor: C.border,
    borderLeftWidth: 3, borderLeftColor: C.accent,
  },
  jerryHeader: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8},
  jerryTitle: {fontSize: 11, fontWeight: '800', color: C.accent, letterSpacing: 1.4, textTransform: 'uppercase'},
  jerryLoadingText: {fontSize: 10, color: C.textMuted, fontStyle: 'italic'},
  jerryBody: {fontSize: 13, color: C.text, lineHeight: 20},
  jerryToggle: {marginTop: 8, fontSize: 11, color: C.accent, fontWeight: '600'},

  // Alignment strip
  alignmentStripWrap: {
    backgroundColor: C.surface2,
    borderBottomWidth: 1, borderBottomColor: C.border,
    paddingVertical: 12,
  },
  alignmentStripInner: {paddingHorizontal: 18, gap: 8},
  alignChip: {
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: 6,
    borderWidth: 1, backgroundColor: C.surface,
    flexDirection: 'row', alignItems: 'center', gap: 5,
  },
  alignChipLabel: {color: C.textMuted, fontWeight: '500', fontSize: 10},
  alignChipValue: {fontWeight: '700', fontSize: 11},

  // Section wrapper
  section: {
    paddingHorizontal: 18, paddingVertical: 16,
    borderBottomWidth: 1, borderBottomColor: C.border,
  },
  sectionTitleRow: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10},
  sectionTitle: {fontSize: 10, fontWeight: '700', color: C.textMuted, textTransform: 'uppercase', letterSpacing: 1.2},
  sectionHint: {fontSize: 10, color: C.textDim, fontStyle: 'italic'},
  emptyMuted: {fontSize: 11, color: C.textDim, fontStyle: 'italic'},

  // Expander
  expander: {borderTopWidth: 1, borderTopColor: C.border},
  expanderSummary: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: 18, paddingVertical: 14,
    gap: 8,
  },
  expanderTitle: {fontSize: 10, fontWeight: '700', color: C.textMuted, textTransform: 'uppercase', letterSpacing: 1.2, flex: 1},
  expanderBadge: {
    fontSize: 10, paddingHorizontal: 6, paddingVertical: 2,
    borderRadius: 10, backgroundColor: C.surface2, color: C.text, fontWeight: '700',
  },
  expanderChevron: {color: C.textDim, fontSize: 14, marginLeft: 4},
  expanderBody: {paddingHorizontal: 18, paddingBottom: 18},

  // Market row
  marketRow: {
    flexDirection: 'row', flexWrap: 'wrap', gap: 14,
    padding: 10, backgroundColor: C.surface2, borderRadius: 8,
  },
  marketItem: {color: C.textMuted, fontSize: 13, fontVariant: ['tabular-nums']},
  marketVal: {color: C.text, fontWeight: '600'},

  // Score
  scoreLine: {flexDirection: 'row', justifyContent: 'center', alignItems: 'baseline', gap: 16, paddingVertical: 8},
  scoreTeam: {alignItems: 'center', gap: 3},
  scoreRuns: {fontSize: 22, fontWeight: '700', fontVariant: ['tabular-nums']},
  scoreTeamAbbr: {fontSize: 10, color: C.textMuted, fontWeight: '600', letterSpacing: 1},
  scoreSep: {color: C.textDim, fontSize: 20, fontWeight: '300'},
  scoreSub: {textAlign: 'center', fontSize: 11, color: C.textMuted, marginTop: 4},
  jerryBanner: {
    marginTop: 10, paddingHorizontal: 10, paddingVertical: 8,
    backgroundColor: C.surface2, borderRadius: 6,
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap',
  },
  jerryLabel: {fontSize: 11, color: C.textMuted, fontWeight: '600'},
  jerryValue: {fontSize: 11, color: C.text, fontVariant: ['tabular-nums']},

  // Pitcher matchup
  pitcherMatchup: {flexDirection: 'row', gap: 8},
  pitcherCard: {
    flex: 1, padding: 10, backgroundColor: C.surface2, borderRadius: 8,
    borderTopWidth: 2, gap: 3,
  },
  pitcherName: {fontSize: 12, fontWeight: '700', color: C.text},
  pitcherStats: {fontSize: 10, color: C.textMuted, fontVariant: ['tabular-nums'], lineHeight: 15},
  pitcherStatBold: {color: C.text, fontWeight: '700'},
  teamProjBanner: {
    marginTop: 8, paddingHorizontal: 10, paddingVertical: 8,
    backgroundColor: C.surface2, borderRadius: 6,
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8,
  },
  teamProjLabel: {fontSize: 11, color: C.textMuted},
  teamProjValue: {fontSize: 11, color: C.text, fontWeight: '600', fontVariant: ['tabular-nums']},

  // Money flow
  moneyMarket: {
    padding: 10, backgroundColor: C.surface2, borderRadius: 8,
    borderLeftWidth: 3, borderLeftColor: C.border,
  },
  moneyMarketHeader: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline',
    marginBottom: 8, flexWrap: 'wrap', gap: 6,
  },
  moneyMarketLabel: {fontSize: 11, fontWeight: '700', color: C.textMuted, textTransform: 'uppercase', letterSpacing: 0.5},
  moneyMarketSide: {fontSize: 12, fontWeight: '700', color: C.text},
  moneyMarketDiv: {fontSize: 12, fontWeight: '700', color: C.text, fontVariant: ['tabular-nums']},
  sharpBadge: {backgroundColor: C.sharp, paddingHorizontal: 6, paddingVertical: 2, borderRadius: 3},
  sharpBadgeText: {color: '#fff', fontSize: 9, fontWeight: '800', letterSpacing: 0.8},
  moneyBarRow: {flexDirection: 'row', alignItems: 'center', gap: 8},
  moneyBarLabel: {width: 42, fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5},
  moneyBarTrack: {flex: 1, height: 8, backgroundColor: C.overlay, borderRadius: 4, overflow: 'hidden'},
  moneyBarFill: {height: 8, borderRadius: 4},
  moneyBarPct: {width: 40, textAlign: 'right', fontSize: 11, fontWeight: '700', color: C.text, fontVariant: ['tabular-nums']},
  moneyDivNote: {
    fontSize: 10, color: C.textMuted, marginTop: 6, paddingTop: 6,
    borderTopWidth: 1, borderTopColor: C.border, borderStyle: 'dashed',
  },

  // Line movement
  lineMoveStrip: {flexDirection: 'row', gap: 8},
  lineMoveItem: {
    flex: 1, padding: 8, backgroundColor: C.surface2, borderRadius: 6,
    gap: 2, minWidth: 0,
  },
  lineMoveLabel: {fontSize: 9, color: C.textMuted, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5},
  lineMoveValues: {fontSize: 12, fontVariant: ['tabular-nums']},
  lineMoveDelta: {fontSize: 10, fontVariant: ['tabular-nums']},

  // Lens grid
  lensGrid: {flexDirection: 'row', gap: 5},
  lens: {
    flex: 1, backgroundColor: C.surface2, borderRadius: 6, padding: 7,
    alignItems: 'center', gap: 3, borderTopWidth: 2,
  },
  lensName: {fontSize: 9, fontWeight: '700', color: C.textMuted, letterSpacing: 0.6, textTransform: 'uppercase'},
  lensMargin: {fontSize: 12, fontWeight: '700', fontVariant: ['tabular-nums']},
  lensTotal: {fontSize: 9, fontVariant: ['tabular-nums']},

  // Handicappers
  handiRow: {
    flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 4, paddingVertical: 6,
  },
  handiSideLabel: {fontSize: 10, color: C.textMuted, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5, marginRight: 6},
  handiGroupLabel: {fontSize: 9, color: C.textDim, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.8, marginTop: 8, marginBottom: 2},

  // NFL slot
  sitChip: {
    paddingHorizontal: 8, paddingVertical: 4, borderRadius: 4,
    borderWidth: 1, backgroundColor: C.surface2,
  },
  sitChipText: {fontSize: 10, fontWeight: '700', letterSpacing: 0.3},
  injSideLabel: {fontSize: 10, color: C.textMuted, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 2},
  injRow: {fontSize: 11, color: C.text, lineHeight: 17, marginBottom: 2},
  handiChip: {
    paddingHorizontal: 7, paddingVertical: 2, backgroundColor: C.surface2,
    borderWidth: 1, borderColor: C.border, borderRadius: 4,
    flexDirection: 'row', alignItems: 'baseline',
  },
  handiChipText: {fontSize: 10, color: C.text},
  handiChipRecord: {fontSize: 9, color: C.textMuted, marginLeft: 3, fontVariant: ['tabular-nums']},
  handiCount: {marginLeft: 'auto', fontSize: 11, color: C.textMuted, fontWeight: '600', fontVariant: ['tabular-nums']},
  handiEmpty: {fontSize: 10, color: C.textDim, fontStyle: 'italic'},

  // Cohorts
  cohortsGrid: {flexDirection: 'row', flexWrap: 'wrap', gap: 4},
  cohort: {
    width: '48%',
    padding: 6, backgroundColor: C.surface2, borderRadius: 5,
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    borderLeftWidth: 2,
  },
  cohortName: {color: C.textMuted, fontSize: 10, fontVariant: ['tabular-nums']},
  cohortSide: {fontSize: 10, fontWeight: '700', letterSpacing: 0.5},

  // Props
  propRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    padding: 8, backgroundColor: C.surface2, borderRadius: 6,
  },
  propTier: {width: 46, paddingVertical: 3, borderRadius: 3, alignItems: 'center'},
  propTierText: {fontSize: 9, fontWeight: '800', letterSpacing: 0.4},
  propPlayer: {fontSize: 12, fontWeight: '600', color: C.text},
  propDetail: {fontSize: 10, color: C.textMuted, fontVariant: ['tabular-nums']},
  propBold: {color: C.text, fontWeight: '700'},

  // HRB tiles
  hrbTiles: {flexDirection: 'row', gap: 6},
  hrbTile: {
    flex: 1, padding: 8, backgroundColor: C.surface2, borderRadius: 6,
    alignItems: 'center', gap: 2,
    borderWidth: 1, borderColor: C.border,
  },
  hrbTileLabel: {fontSize: 9, color: C.textMuted, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5},
  hrbTileVal: {fontSize: 13, fontWeight: '700', color: C.text, fontVariant: ['tabular-nums']},
  hrbTileOdds: {fontSize: 10, color: C.textMuted, fontVariant: ['tabular-nums']},
  hrbSelectionHint: {marginTop: 10, fontSize: 11, color: C.textMuted, textAlign: 'center'},
  parlayCta: {
    marginTop: 12, padding: 12, backgroundColor: C.accent, borderRadius: 8, alignItems: 'center',
  },
  parlayCtaText: {color: '#000', fontWeight: '700', fontSize: 13, letterSpacing: 0.3},

  // All book lines table
  bookTableHeader: {
    flexDirection: 'row', paddingVertical: 6, paddingHorizontal: 4,
    borderBottomWidth: 1, borderBottomColor: C.border, gap: 6, marginBottom: 4,
  },
  bookTh: {fontSize: 9, color: C.textMuted, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5},
  bookTableRow: {
    flexDirection: 'row', paddingVertical: 6, paddingHorizontal: 4, gap: 6,
    borderBottomWidth: 1, borderBottomColor: C.border,
  },
  bookTd: {fontSize: 11, color: C.text, fontVariant: ['tabular-nums']},

  // Numbers
  numbersHeading: {fontSize: 10, color: C.textMuted, fontWeight: '700', letterSpacing: 0.6, textTransform: 'uppercase', marginBottom: 4},
  numbersTable: {},
  numbersTableRow: {flexDirection: 'row', paddingVertical: 4, borderBottomWidth: 1, borderBottomColor: C.border, gap: 6},
  numbersTh: {fontSize: 10, color: C.textMuted, fontWeight: '600', letterSpacing: 0.4, textTransform: 'uppercase'},
  numbersTd: {fontSize: 11, color: C.text, fontVariant: ['tabular-nums']},
  numbersMCGrid: {flexDirection: 'row', flexWrap: 'wrap', gap: 4},
  mcTile: {
    width: '48%',
    padding: 6, backgroundColor: C.surface2, borderRadius: 4,
    flexDirection: 'row', justifyContent: 'space-between',
  },
  mcTileLabel: {color: C.textMuted, fontSize: 11},
  mcTileValue: {color: C.text, fontWeight: '700', fontVariant: ['tabular-nums'], fontSize: 11},
  numbersMono: {fontSize: 11, color: C.textMuted, fontVariant: ['tabular-nums']},

  // Footer
  footer: {padding: 18, alignItems: 'center'},
  footerText: {fontSize: 10, color: C.textDim, letterSpacing: 0.6, textTransform: 'uppercase'},
});
