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
  textDim: '#556270',
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
            const {data: ctxData} = await client
              .from(contextTable)
              .select('game_id,game_date')
              .eq('game_date', gameDate)
              .eq('home_team', home)
              .eq('away_team', away)
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

  const awayTeam = game.away_team || ctx?.away_team || 'Away';
  const homeTeam = game.home_team || ctx?.home_team || 'Home';
  const closeSpread = ctx?.close_spread ?? game.close_spread;
  const closeTotal = ctx?.close_total ?? game.close_total;
  const homeML = ctx?.home_ml_close ?? game.home_ml;
  const awayML = ctx?.away_ml_close ?? game.away_ml;

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

        <Section title="Predicted Score" hint="range across models">
          <ScoreRange ctx={ctx} awayTeam={awayTeam} homeTeam={homeTeam} />
        </Section>

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

        <Section title="Model Consensus" hint="margin (H+ / A−)">
          <LensGrid ctx={ctx} gamesSport={gamesSport} />
        </Section>

        <Section title="External Handicappers">
          <HandicappersRow picks={externalPicks} homeTeam={homeTeam} awayTeam={awayTeam} sport={gamesSport} />
        </Section>

        <SportSpecificSlot ctx={ctx} gamesSport={gamesSport} game={game} />

        <Expander title="Cohort Signals" badge={cohortBadge(ctx)}>
          <CohortsPanel ctx={ctx} />
        </Expander>

        <Expander title="Game Props" badge={`${gameProps.length} signal${gameProps.length === 1 ? '' : 's'}`}>
          <GamePropsPanel props={gameProps} />
        </Expander>

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

        <Expander title="All Book Lines" badge={`${(game.bookmakers || []).length} books`}>
          <AllBookLinesPanel
            bookmakers={game.bookmakers || []}
            homeTeam={homeTeam}
            awayTeam={awayTeam}
            onAddParlayLeg={onAddParlayLeg}
          />
        </Expander>

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
  const tier = play.tier || '—';
  const label = play.label || '';
  const sub = play.sub || '';
  const tierStyle = tierBadgeStyle(tier);
  return (
    <View style={styles.verdict}>
      <View style={[styles.verdictTierPill, tierStyle]}>
        <Text style={[styles.verdictTierText, {color: tierStyle.color}]}>
          {tier} · {String(play.type || '').toUpperCase()}
        </Text>
      </View>
      <Text style={styles.verdictPlay}>{label}</Text>
      {sub ? <Text style={styles.verdictWhy}>{sub}</Text> : null}
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
      {synthesis?.call_text && (
        <View style={{flexDirection:'row',alignItems:'center',gap:6,marginBottom:8,flexWrap:'wrap'}}>
          <View style={{backgroundColor:chipColor + '22',borderColor:chipColor + '44',borderWidth:1,paddingHorizontal:10,paddingVertical:4,borderRadius:8}}>
            <Text style={{color:chipColor,fontWeight:'800',fontSize:13}}>{synthesis.call_text} · {conv}</Text>
          </View>
          {isAmRead && (
            <Text style={{color:C.textMuted,fontSize:10,fontStyle:'italic'}}>AM read · refreshes 2pm ET</Text>
          )}
        </View>
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
function ScoreRange({ctx, awayTeam, homeTeam}: any) {
  const mc = safeJSON(ctx?.mc_probabilities) || {};
  const preds: {name: string; a: number; h: number}[] = [];
  const addPred = (name: string, tot: any, mgn: any) => {
    if (tot == null || mgn == null) return;
    const t = parseFloat(tot); const m = parseFloat(mgn);
    if (!isFinite(t) || !isFinite(m)) return;
    preds.push({name, a: (t - m) / 2, h: (t + m) / 2});
  };
  addPred('Panel', ctx?.panel_implied_total, ctx?.panel_implied_margin);
  addPred('Jerry', ctx?.jerry_pred_total, ctx?.jerry_pred_spread);
  addPred('v3', ctx?.projected_total, ctx?.projected_spread);
  addPred('v4', ctx?.model_pred_total, ctx?.model_pred_spread);
  addPred('MC', mc.mc_expected_total ?? mc.mc_mean_total, mc.mc_expected_margin);

  if (preds.length === 0) return <Text style={styles.emptyMuted}>No score projections available.</Text>;

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

function MoneyFlow({ctx, sport}: any) {
  const oc = ctx?.oddscrowd_snapshot;
  if (!oc || typeof oc !== 'object') {
    // No source attribution. When money data is missing, we say nothing about
    // provenance (competitive moat — see feedback re: Action Network model).
    // Hidden rather than "no data" copy since presence is itself a signal.
    return null;
  }
  const markets: {key: 'ml'|'rl'|'total'; label: string; data: any}[] = [
    {key: 'ml', label: 'Moneyline', data: oc.ml},
    {key: 'rl', label: rlLabel(sport), data: oc.rl},
    {key: 'total', label: 'Total', data: oc.total},
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
  const closeHomeML = ctx?.home_ml_close;

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
function LensGrid({ctx, gamesSport}: any) {
  const mc = safeJSON(ctx?.mc_probabilities) || {};
  const rows = gamesSport === 'MLB' ? [
    {name: 'Panel', m: ctx?.panel_implied_margin, t: ctx?.panel_implied_total},
    {name: 'Jerry', m: ctx?.jerry_pred_spread, t: ctx?.jerry_pred_total},
    {name: 'v3', m: ctx?.projected_spread, t: ctx?.projected_total},
    {name: 'v4', m: ctx?.model_pred_spread, t: ctx?.model_pred_total},
    {name: 'MC', m: mc.mc_expected_margin, t: mc.mc_expected_total ?? mc.mc_mean_total},
  ] : [
    // Non-MLB sports have fewer lens fields
    {name: 'v3', m: ctx?.projected_spread, t: ctx?.projected_total},
    {name: 'v4', m: ctx?.model_pred_spread, t: ctx?.model_pred_total},
    {name: 'Conf', m: ctx?.signal_confluence_net, t: null},
  ];

  const closeTot = ctx?.close_total;

  return (
    <View style={styles.lensGrid}>
      {rows.map((r, i) => {
        const mgnSide = signSide(r.m);
        const totDir = r.t != null && closeTot != null
          ? (r.t > closeTot ? 'O' : r.t < closeTot ? 'U' : '=')
          : null;
        const missing = r.m == null;
        return (
          <View
            key={i}
            style={[
              styles.lens,
              {borderTopColor: missing ? C.border : sideColor(mgnSide), opacity: missing ? 0.5 : 1},
            ]}
          >
            <Text style={styles.lensName}>{r.name}</Text>
            <Text style={[styles.lensMargin, {color: missing ? C.textDim : sideColor(mgnSide)}]}>
              {missing ? '—' : (r.m > 0 ? `+${f(r.m, 2)}` : f(r.m, 2))}
            </Text>
            <Text style={[styles.lensTotal, {
              color: totDir === 'O' ? C.accent : totDir === 'U' ? C.sharp : C.textMuted,
            }]}>
              {r.t == null ? '—' : `${totDir ?? '='} ${f(r.t, 1)}`}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

// ─── HANDICAPPERS ROW ───────────────────────────────────────────────────
function HandicappersRow({picks, homeTeam, awayTeam, sport}: any) {
  const nonOC = (picks || []).filter((p: any) => p.source !== 'oddscrowd');
  const ml = nonOC.filter((p: any) => p.surface === 'ml');
  const rl = nonOC.filter((p: any) => p.surface === 'rl');
  const totals = nonOC.filter((p: any) => p.surface === 'total');

  const mlHome = ml.filter((p: any) => p.pick_side === 'HOME');
  const mlAway = ml.filter((p: any) => p.pick_side === 'AWAY');
  const rlHome = rl.filter((p: any) => p.pick_side === 'HOME');
  const rlAway = rl.filter((p: any) => p.pick_side === 'AWAY');
  const totOver = totals.filter((p: any) => p.pick_side === 'OVER');
  const totUnder = totals.filter((p: any) => p.pick_side === 'UNDER');

  const chip = (p: any, i: number) => (
    <View
      key={`${p.source}-${i}`}
      style={[
        styles.handiChip,
        p.fade_flag === 'boost' && {backgroundColor: C.accentDim, borderColor: C.accent},
        p.fade_flag === 'fade' && {backgroundColor: C.fadeDim, borderColor: C.fade},
      ]}>
      <Text style={[
        styles.handiChipText,
        p.fade_flag === 'boost' && {color: C.accent},
        p.fade_flag === 'fade' && {color: C.fade},
      ]}>
        {p.source}
      </Text>
    </View>
  );

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
      {nonOC.length === 0 && (
        <Text style={styles.handiEmpty}>No handicapper picks pulled yet.</Text>
      )}
    </View>
  );
}

// ─── SPORT-SPECIFIC SLOT ─────────────────────────────────────────────────
function SportSpecificSlot({ctx, gamesSport, game}: any) {
  if (gamesSport === 'MLB') {
    // Pitcher card lives in Stat Projections above; no additional slot needed
    return null;
  }
  if (gamesSport === 'UFC') {
    return (
      <Section title="Fighter Reads">
        <Text style={styles.emptyMuted}>Fighter breakdown card coming next (reach/reads + method/round breakdown).</Text>
      </Section>
    );
  }
  if (gamesSport === 'NFL' || gamesSport === 'NCAAF') {
    return <NFLSlot ctx={ctx} game={game} />;
  }
  if (gamesSport === 'NBA' || gamesSport === 'NCAAB') {
    return (
      <Section title="Pace + Rating">
        <Text style={styles.emptyMuted}>Pace / net-rating card + rest days coming next.</Text>
      </Section>
    );
  }
  if (gamesSport === 'NHL') {
    return (
      <Section title="Goalie + Rest">
        <Text style={styles.emptyMuted}>Goalie matchup + B2B chip coming next.</Text>
      </Section>
    );
  }
  return null;
}

// ─── NFL SLOT ────────────────────────────────────────────────────────────
// Phase 1 (2026-07-30) — renders what's available from nfl_game_context +
// nfl_team_stats. Phase 2 adds QB starter card + injuries + weather when
// those pipes ship.
function NFLSlot({ctx, game}: any) {
  const [teamStats, setTeamStats] = useState<{home?: any; away?: any} | null>(null);
  const [starters, setStarters] = useState<{home?: any; away?: any} | null>(null);
  const [injuries, setInjuries] = useState<{home: any[]; away: any[]}>({home: [], away: []});

  const homeTeam = ctx?.home_team || game?.home_team;
  const awayTeam = ctx?.away_team || game?.away_team;

  useEffect(() => {
    const client = sb();
    if (!client || !homeTeam || !awayTeam) return;
    (async () => {
      const season = ctx?.season || new Date().getFullYear();
      // Season team stats — try current season, fall back to prior season
      const {data: ts} = await client
        .from('nfl_team_stats')
        .select('team,pass_epa,rush_epa,pass_yards,rush_yards,pass_attempts,rush_attempts,pass_tds,pass_ints,def_sacks,def_ints,def_pass_def,pass_cpoe,sacks_suffered,games,season')
        .in('team', [homeTeam, awayTeam])
        .lte('season', season)
        .eq('season_type', 'REG')
        .order('season', {ascending: false})
        .limit(6);
      if (ts) {
        // Pick most-recent per team
        const map: any = {};
        for (const row of ts) {
          if (!map[row.team]) map[row.team] = row;
        }
        setTeamStats({home: map[homeTeam], away: map[awayTeam]});
      }
      // Starters (Phase 2 table — nfl_starters, populated by nfl_weekly_starters.py)
      const {data: st} = await client.from('nfl_starters')
        .select('team,position,player_name,is_starter')
        .in('team', [homeTeam, awayTeam])
        .eq('position', 'QB')
        .eq('is_starter', true)
        .order('week', {ascending: false})
        .limit(2);
      if (st) {
        const smap: any = {};
        for (const row of st) if (!smap[row.team]) smap[row.team] = row;
        setStarters({home: smap[homeTeam], away: smap[awayTeam]});
      }
      // Injuries (Phase 2 table — nfl_injuries)
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
  }, [homeTeam, awayTeam, ctx?.season]);

  const tags = ctx?.cohort_tags || [];
  const rest = {home: ctx?.home_rest, away: ctx?.away_rest};
  const wx = {temp: ctx?.temp, wind: ctx?.wind};
  const roof = ctx?.roof;
  const div = ctx?.div_game;

  return (
    <>
      {/* Starter QBs (if pipe has populated) */}
      {(starters?.home || starters?.away) && (
        <Section title="Starting QBs" hint="from nfl_starters">
          <View style={styles.pitcherMatchup}>
            <View style={[styles.pitcherCard, {borderTopColor: C.away}]}>
              <Text style={styles.pitcherName}>{starters?.away?.player_name || 'TBD'}</Text>
              <Text style={styles.pitcherStats}>{abbrev3(awayTeam)} QB</Text>
            </View>
            <View style={[styles.pitcherCard, {borderTopColor: C.home}]}>
              <Text style={styles.pitcherName}>{starters?.home?.player_name || 'TBD'}</Text>
              <Text style={styles.pitcherStats}>{abbrev3(homeTeam)} QB</Text>
            </View>
          </View>
        </Section>
      )}

      {/* Team offense/defense — always try, degrades w/ '—' when missing */}
      <Section title="Team Matchup" hint="season · pass EPA + def">
        {teamStats ? (
          <View style={{gap: 8}}>
            <TeamStatRow team={awayTeam} side="away" stats={teamStats.away} defense={teamStats.home} />
            <TeamStatRow team={homeTeam} side="home" stats={teamStats.home} defense={teamStats.away} />
          </View>
        ) : (
          <Text style={styles.emptyMuted}>Loading team stats…</Text>
        )}
      </Section>

      {/* Injuries (if populated) */}
      {(injuries.home.length + injuries.away.length) > 0 && (
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
      )}

      {/* Situational chips row — only render if at least one is present */}
      {(div || roof || wx.temp != null || wx.wind != null || rest.home != null || rest.away != null || (tags && tags.length)) && (
        <Section title="Situational">
          <View style={{flexDirection: 'row', flexWrap: 'wrap', gap: 6}}>
            {div && <SitChip label="Divisional" />}
            {roof && <SitChip label={`Roof: ${roof}`} />}
            {wx.temp != null && <SitChip label={`${wx.temp}°F`} kind={wx.temp <= 40 ? 'info' : 'neutral'} />}
            {wx.wind != null && wx.wind >= 15 && <SitChip label={`Wind ${wx.wind}mph`} kind="warn" />}
            {rest.home != null && rest.away != null && Math.abs(rest.home - rest.away) >= 3 && (
              <SitChip label={`Rest gap: ${abbrev3(rest.home > rest.away ? homeTeam : awayTeam)} +${Math.abs(rest.home - rest.away)}d`} kind="info" />
            )}
            {Array.isArray(tags) && tags.map((t: string, i: number) => (
              <SitChip key={i} label={t.replace(/^nfl_/, '').replace(/_/g, ' ')} />
            ))}
          </View>
        </Section>
      )}
    </>
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
  },
  handiChipText: {fontSize: 10, color: C.text},
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
