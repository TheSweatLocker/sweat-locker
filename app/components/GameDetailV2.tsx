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
import {View, Text, TouchableOpacity, ScrollView, StyleSheet, Platform, Alert} from 'react-native';
import {createClient} from '@supabase/supabase-js';
import Explainer from './Explainer';
import { personaFor, scrubSourceNames } from '../lib/sourcePersona';
import { abbrev as teamAbbrev } from '../lib/teamAbbrev';
// 2026-09-08 server-controlled render manifest: lets us hide/rename UI
// sections via `config_ui_sections` SQL edits instead of binary rebuilds.
// See project_ui_toggle_infrastructure_908 memory + migration 20260908a.
// Every new <Section> going forward should wrap in useSectionEnabled(...).
import { useSectionEnabled } from '../lib/uiConfig';

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
  // 2026-09-25: these four were USED but never DEFINED — 28 references to
  // keys that did not exist on this object. Andy: "colors of stats are dark
  // not green or red."
  //
  // `{color: C.win}` with C.win undefined does not fall back to the inherited
  // colour: in a RN style array the later entry still wins, so the explicit
  // undefined clears the colour and the platform default (black) renders. On
  // C.bg #0e1116 that is invisible, which is exactly the Team Stats screenshot
  // — every advantaged value went black while neutral rows, which never enter
  // that branch, stayed readable.
  //
  // Worse where it is concatenated: `C.win + '30'` evaluates to the STRING
  // "undefined30", an invalid colour that RN drops. That is why RankChip
  // showed amber (C.warn) and blue (C.sharp) pills but never a green or red
  // one — the exact two tiers the card's "green = better" legend promises.
  //
  // borderStrong and surface3 were defined and unused, so this reads as a
  // rename whose call sites were never updated. Values match the sibling
  // components (LineMovementTab, UfcFightDetail) so win/loss are one colour
  // across the app.
  win: '#4ade80',
  loss: '#f87171',
  surfaceAlt: '#232c39',
  borderSoft: '#2a3341',
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
  // 2026-09-06 paywall: Jerry per-game read gated behind Pro. isPro drives
  // whether the section renders full narrative (Pro) or a locked preview
  // + upgrade CTA (free). onUpgrade opens the shared Paywall modal — bubble
  // up rather than importing the modal here so state stays centralized.
  isPro?: boolean;
  onUpgrade?: () => void;
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
  // 2026-09-05 delegate to the shared abbrev() (teamAbbrev.ts) which
  // consults BOTH the TEAM_ABBREV canonical map AND the ALIASES map
  // (NCAAF/NCAAB college teams live in ALIASES). Prior local fallback
  // (last-word first-3-chars) produced STA for "New Mexico State" and
  // MER for "Mercyhurst" — obviously wrong. abbrev() falls back to the
  // same last-word slice ONLY when no lookup hit.
  return teamAbbrev(team) || team.split(' ').slice(-1)[0].slice(0, 3).toUpperCase();
};

// 2026-09-10: Cohort tag + confluence key → user-facing label. Mirrors the
// seed rows in cohort_display_config (migration 20260910b) so the client
// renders proper Title-Case labels immediately even before the fetch cache
// lands. Backend remains authoritative — this map is the fallback; when we
// wire fetchCohortLabels() we'll prefer the DB row and only fall back here.
const COHORT_LABEL_MAP: Record<string, string> = {
  // NFL cohort_tags (arrive with 'nfl_' prefix in ctx.cohort_tags)
  'nfl_home_fav':        'Home Favorite',
  'nfl_heavy_home_dog':  'Heavy Home Underdog',
  'nfl_div_home_cover':  'Divisional Home Cover',
  // NCAAF cohort_tags
  'ncaaf_home_fav':        'Home Favorite',
  'ncaaf_heavy_home_fav':  'Heavy Home Favorite',
  'ncaaf_heavy_home_dog':  'Heavy Home Underdog',
  'ncaaf_shootout':        'Projected Shootout',
  'ncaaf_grinder':         'Projected Grinder',
  // Confluence keys (bare — arrive in signal_confluence_breakdown JSONB)
  'hfa':            'Home-Field Edge',
  'cpoe':           'QB Accuracy (CPOE)',
  'def_splash':     'Defensive Splash',
  'off_epa':        'Offensive EPA',
  'rush_epa':       'Rush EPA',
  'sp_plus':        'SP+ Rating',
  'def_epa':        'Defensive EPA',
  'explosiveness':  'Explosiveness',
  'success_rate':   'Success Rate',
};
// Force Title Case fallback for any tag missing from the map, so "home fav"
// no longer sits next to "Divisional" in a chip row — the capitalization bug
// user flagged 9/10. Strips known sport prefixes first.
// 2026-09-10 HARDENING: guard against null/undefined input (a tag with a
// weird key was crashing .replace() on a non-string during game-detail
// render — belt-and-suspenders against the caller passing anything).
const titleCaseFallback = (raw: any): string => {
  const s = String(raw ?? '');
  if (!s) return '';
  return s
    .replace(/^(nfl|ncaaf|ncaab|nba|nhl|mlb|ufc)_/i, '')
    .split('_')
    .map(tok => tok.length ? tok.charAt(0).toUpperCase() + tok.slice(1) : '')
    .join(' ');
};
const prettyCohortTag = (raw: any): string => {
  const key = String(raw ?? '');
  return COHORT_LABEL_MAP[key] || titleCaseFallback(key);
};

// ─── Main component ─────────────────────────────────────────────────────
export default function GameDetailV2({
  game, ctx, gamesSport, externalPicks: externalPicksProp, gameProps: gamePropsProp,
  historicalOdds, jerryNarrative, jerrySynthesis, jerryLoading, onClose, onAddParlayLeg, onLogPick,
  isPro, onUpgrade,
}: Props) {
  const [fetchedExternals, setFetchedExternals] = useState<any[]>([]);
  const [fetchedProps, setFetchedProps] = useState<any[]>([]);
  const [sourceRecords, setSourceRecords] = useState<Record<string, any>>({});
  // 2026-09-10 · cohort_tag_records lookup — per (tag, market) historical W-L
  // populated by mlb_pipeline/build_cohort_tag_records.py, one fetch per game
  // detail mount for the current sport (max ~10 rows). Chips display the ATS
  // record for situational-side tags, total record for O/U-side tags.
  const [cohortTagRecords, setCohortTagRecords] = useState<Record<string, any>>({});

  // 2026-09-08 server-controlled section toggles for the shared universal
  // sections. sport='ALL' at the DB level so a single row hides across
  // every sport; can override per-sport later by adding a specific row.
  // Sport passed via `gamesSport` prop — falls through to 'ALL' when a
  // sport-specific row is absent, per uiConfig.ts lookup order.
  const _sport = (gamesSport || 'ALL').toUpperCase();
  const showMarket         = useSectionEnabled(_sport, 'game_detail', 'market',                true);
  const showPredictedScore = useSectionEnabled(_sport, 'game_detail', 'predicted_score',       true);
  const showStatProjections = useSectionEnabled(_sport, 'game_detail', 'stat_projections',     true);
  const showMoneyFlow      = useSectionEnabled(_sport, 'game_detail', 'money_flow',            true);
  // 2026-09-25: Public Splits was the one section with no toggle — it rendered
  // on `ctx?.splits_summary` alone, so it could never be turned off without a
  // build, against the convention every other section follows.
  //
  // Andy: "for the public splits in game details lets remove it, money flow
  // shows the data." It does — both render money% vs bets% off the same split
  // sources; Public Splits was the raw pair and Money Flow is the divergence
  // read on it. Two panels, one dataset, and the weaker framing of the two.
  //
  // Defaults FALSE so it is hidden as soon as this build ships, and the row in
  // config_ui_sections can bring it back with no App Store round-trip.
  const showPublicSplits   = useSectionEnabled(_sport, 'game_detail', 'public_splits',          false);
  const showLineMovement   = useSectionEnabled(_sport, 'game_detail', 'line_movement',         true);
  const showModelConsensus = useSectionEnabled(_sport, 'game_detail', 'model_consensus',       true);
  const showExternalHandicappers = useSectionEnabled(_sport, 'game_detail', 'external_handicappers', true);
  const showRecentSchedule = useSectionEnabled(_sport, 'game_detail', 'recent_schedule',       true);
  const showSituationalRec = useSectionEnabled(_sport, 'game_detail', 'situational_records',   true);
  const showTeamStats      = useSectionEnabled(_sport, 'game_detail', 'team_stats',            true);
  const showSportsbookOdds = useSectionEnabled(_sport, 'game_detail', 'sportsbook_odds',       true);

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
            : gamesSport === 'NCAAB' ? 'ncaab_game_context'
            // 2026-09-28: NHL was absent, so this fallback could never
            // resolve a game_id for an NHL game and the externals fetch
            // bailed out at the `!gid` guard below.
            : gamesSport === 'NHL' ? 'nhl_game_context' : null;
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
        // 2026-09-25: dropped .eq('game_date', gameDate). game_id already
        // identifies the game uniquely, and the date equality was actively
        // hiding sources: most pullers stamped the pick with the PULL date,
        // not the game's date, so a Sunday game pulled on Tue/Wed/Thu wrote
        // rows under four dates and this filter matched none of them.
        // ARI @ SF had 9 external picks and rendered ONE — covers, the only
        // source that already stamped the real date. The puller is fixed
        // going forward; dropping the filter also recovers every historical
        // row that is already mis-dated.
        const {data: extData, error: extErr} = await client
          .from('external_picks')
          .select('source,surface,pick_side,confidence,fade_flag,pick_line,odds_american,game_date,pulled_at')
          .eq('sport', gamesSport)
          .eq('game_id', gid);
        if (extErr) console.warn('[GameDetailV2] externals fetch error:', extErr.message);
        if (!cancelled && extData) {
          // Collapse the repeats the mis-dating created: one row per
          // source × surface × side, keeping the most recent pull. Without
          // this the same tout renders as four identical chips.
          const latest = new Map<string, any>();
          for (const e of extData) {
            const k = `${e.source}::${e.surface}::${e.pick_side}`;
            const prev = latest.get(k);
            if (!prev || String(e.pulled_at || '') > String(prev.pulled_at || '')) {
              latest.set(k, e);
            }
          }
          const deduped = Array.from(latest.values());
          console.log(`[GameDetailV2] fetched ${extData.length} external_picks for gid=${gid}, ${deduped.length} after dedup`);
          setFetchedExternals(deduped);
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

      // 2026-09-10 · fetch cohort_tag_records for this sport (NFL/NCAAF only
      // for now; MLB/NBA/etc. rollups follow). Empty result is fine — the
      // chip renderer just skips the "· 45-40 (52.9%)" line and shows the
      // pretty label alone. Kept cheap by filtering to the game's sport.
      if (gamesSport === 'NFL' || gamesSport === 'NCAAF') {
        const {data: recData, error: recErr} = await client
          .from('cohort_tag_records')
          .select('tag,market,wins,losses,pushes,hit_rate,sample_n')
          .eq('sport', gamesSport)
          .eq('season_scope', 'lifetime')
          .eq('side', 'primary');
        if (recErr && recErr.code !== 'PGRST205') {
          console.warn('[GameDetailV2] cohort_tag_records fetch error:', recErr.message);
        }
        if (!cancelled && recData) {
          const map: Record<string, any> = {};
          for (const r of recData) map[`${r.tag}|${r.market}`] = r;
          setCohortTagRecords(map);
        }
      }

      // Fetch props (MLB only for now).
      // 2026-09-15 CRITICAL: was reading raw mlb_pipeline_props which
      // bypasses view Rules 4/5/6/7 (batter-family bans, COVERAGE tier
      // block, LEAN hits ban). Result: banned families (runs/rbis/
      // total_bases/hr and hits_over) leaked into the Game Props section
      // of game detail modal even though Prop Jerry hid them. Andy hit
      // this on ATH @ TB: Heim total_bases_under, Aranda rbis_under,
      // Mesa runs_over etc. all showed PRIME here. Fix: use the same
      // view Prop Jerry uses — v_mlb_props_publishable — so every ban
      // applies universally.
      if ((!gamePropsProp || gamePropsProp.length === 0) && gamesSport === 'MLB') {
        // 2026-10-09: added book_over_odds / book_under_odds and the three
        // jerry_* columns. They were ALREADY IN THE VIEW and simply not
        // selected, so the panel rendered a prop with NO PRICE — a user
        // could not see a -250 offer on a prop whose own publishable band is
        // -300..+150, nor the Batter Hits O0.5 juice trap. Price is the field
        // that decides whether a pick makes money; omitting it was the worst
        // gap in this panel. Same explicit-SELECT-is-a-silent-blank trap this
        // file documents elsewhere.
        const {data: propData, error: propErr} = await client
          .from('v_mlb_props_publishable')
          .select('player_name,player_team,prop_type,direction,prop_line,display_conviction,tier,signals,'
                  + 'book_over_odds,book_under_odds,jerry_short_read,jerry_verdict,jerry_conviction')
          .eq('game_date', gameDate)
          .eq('game_id', gid)
          .order('display_conviction', {ascending: false})
          .limit(40);
        if (propErr) console.warn('[GameDetailV2] props fetch error:', propErr.message);
        if (!cancelled && propData) {
          // View returns display_conviction; map to conviction for downstream
          // GamePropsPanel that expects that key.
          setFetchedProps(propData.map((p: any) => ({...p, conviction: p.display_conviction})));
        }
      }
      // ══ 2026-10-09 · NHL PROPS, INFORMATION ONLY ═══════════════════════
      // Andy's call after seeing the numbers: NHL props are surfaced as DATA,
      // never as plays. Measured on 4,247 graded props in the publishable
      // band: 53.78% hit against 57.14% needed, -6.57% ROI, and the loss is a
      // clean directional bias — every OVER family loses 11-21% while every
      // UNDER is flat. No NHL prop family is demonstrably profitable, so
      // nothing here may carry a play badge. See
      // project_nhl_prop_over_bias_1009.
      //
      // Reads ctx_game_id, NOT game_id. nhl_pipeline_props.game_id is an MD5
      // hash while the id this component holds is the NHL numeric id — two
      // ID spaces, which is why filtering by game_id returned zero rows for
      // every NHL game (project_nhl_props_cannot_join_their_game_1003).
      // Bridged by migration 20261009a + bridge_nhl_prop_game_ids.py.
      //
      // There is no v_nhl_props_publishable view, so the -300..+150 odds band
      // is applied HERE. Reading a raw pipeline table without the view's
      // rules is exactly what leaked banned families into this panel on
      // 09-15, so the band is not optional.
      if ((!gamePropsProp || gamePropsProp.length === 0) && gamesSport === 'NHL') {
        const {data: nhlData, error: nhlErr} = await client
          .from('nhl_pipeline_props')
          .select('player_name,team_abbrev,prop_type,direction,prop_line,conviction,tier,'
                  + 'book_over_odds,book_under_odds,player_season_hit_pct,'
                  + 'player_l10_hit_count,player_position')
          .eq('game_date', gameDate)
          .eq('ctx_game_id', String(gid))
          .limit(400);
        if (nhlErr) console.warn('[GameDetailV2] NHL props fetch error:', nhlErr.message);
        if (!cancelled && nhlData) {
          const inBand = nhlData.filter((p: any) => {
            const o = String(p.direction || '').toLowerCase() === 'over'
              ? p.book_over_odds : p.book_under_odds;
            const n = Number(o);
            return o != null && Number.isFinite(n) && n >= -300 && n <= 150;
          });
          // _infoOnly is what suppresses the tier pill downstream. Tagging
          // the rows rather than branching on sport inside the panel keeps
          // the rule with the data that earned it.
          setFetchedProps(inBand.map((p: any) => ({
            ...p, player_team: p.team_abbrev, _infoOnly: true,
            _totalBeforeCap: inBand.length,
          })));
        }
      }

      // ══ 2026-10-10 B69 · NFL PROPS ════════════════════════════════════
      // Game detail fetched props for MLB and NHL only, so NFL props never
      // appeared here at all — despite 46 publishable rows for this week,
      // every one carrying a price AND a Jerry read.
      //
      // v_nfl_props_publishable has the same 21-column shape as the MLB view,
      // so the ban rules live in the view exactly as they do for MLB and this
      // does NOT re-implement them client-side (unlike NHL, which has no
      // view and therefore needs the band applied here).
      //
      // NOT SELECTED: player_season_hit_pct. It exists on nfl_pipeline_props
      // but NOT on this view, and selecting a column a view does not have is
      // a 400 that returns zero rows. The season figure simply does not
      // render for NFL — the explicit-SELECT-is-a-silent-blank trap, handled
      // by checking the view's columns first rather than copying the MLB
      // select verbatim.
      //
      // game_id matches: v_nfl_props_publishable and nfl_game_context both
      // use the 32-char hash (verified), unlike NHL where the two ID spaces
      // differ and a ctx_game_id bridge was required.
      //
      // NCAAF/NCAAB stay excluded deliberately — no props in college
      // (feedback_college_sports_no_props).
      if ((!gamePropsProp || gamePropsProp.length === 0) && gamesSport === 'NFL') {
        const {data: nflData, error: nflErr} = await client
          .from('v_nfl_props_publishable')
          .select('player_name,player_team,prop_type,direction,prop_line,'
                  + 'display_conviction,tier,signals,book_over_odds,'
                  + 'book_under_odds,jerry_short_read,jerry_verdict,'
                  + 'jerry_conviction')
          .eq('game_date', gameDate)
          .eq('game_id', String(gid))
          .order('display_conviction', {ascending: false})
          .limit(40);
        if (nflErr) console.warn('[GameDetailV2] NFL props fetch error:', nflErr.message);
        if (!cancelled && nflData) {
          setFetchedProps(nflData.map((p: any) => ({
            ...p, conviction: p.display_conviction,
            _totalBeforeCap: nflData.length,
          })));
        }
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
        {/* Free tier gets Market card only (odds are free everywhere, no
            competitive advantage in hiding them). Verdict + Jerry read +
            everything below the Market card is Pro. See the big gate
            after the Market section below. */}
        {showMarket && (
        <Section title="Market">
          <MarketRow
            closeSpread={closeSpread}
            closeTotal={closeTotal}
            homeML={homeML}
            awayML={awayML}
            homeTeam={homeTeam}
            awayTeam={awayTeam}
          />
        </Section>
        )}

        {/* 2026-09-06 GAME DETAIL BULK GATE. Everything analytical is Pro:
            Verdict, Jerry read, alignment strip, predicted score, money
            flow, line movement, model consensus, external handicappers,
            situational records, team stats, splits, cohorts, props, book
            lines, numbers dump. One big "unlock the analysis" panel here
            for free users; full render below for Pro. Trade-off: free
            users see the game exists + market prices (parity with any
            free odds app) but the analytical value that drives the
            subscription is behind the wall. */}
        {isPro === false && (
          <View style={{paddingHorizontal: 14, marginTop: 8, marginBottom: 20}}>
            <View style={{backgroundColor: C.accent + '14', borderRadius: 14, padding: 20, borderWidth: 1.5, borderColor: C.accent + '55'}}>
              <View style={{alignItems:'center', marginBottom: 14}}>
                <View style={{backgroundColor: C.accent + '22', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, marginBottom: 10}}>
                  <Text style={{color: C.accent, fontSize: 10, fontWeight: '800', letterSpacing: 1.5}}>🔒 SWEAT LOCKER PRO</Text>
                </View>
                <Text style={{color: C.text, fontSize: 18, fontWeight: '800', textAlign: 'center', marginBottom: 6}}>Unlock the full analysis</Text>
                <Text style={{color: C.textDim, fontSize: 12, lineHeight: 18, textAlign: 'center'}}>
                  Every model, every signal, every counter-argument that goes into our pick — laid out for you to judge.
                </Text>
              </View>
              <View style={{gap: 6, marginTop: 4, marginBottom: 14}}>
                {[
                  'The Verdict — our pick + conviction',
                  "Jerry's Read — full model narrative",
                  'Predicted Score & Model Consensus',
                  'Money Flow — sharp $ vs public bets',
                  'External Handicappers — 8+ sources',
                  'Situational Records & Team Stats',
                  'Line Movement + Public Splits',
                  'Full Model Numbers Dump',
                  'Receipts on every sport — every pick graded nightly',
                ].map((b, i) => (
                  <View key={i} style={{flexDirection: 'row', alignItems: 'flex-start', gap: 8}}>
                    <Text style={{color: C.accent, fontSize: 13, marginTop: 0}}>✓</Text>
                    <Text style={{color: C.textMuted, fontSize: 12, lineHeight: 17, flex: 1}}>{b}</Text>
                  </View>
                ))}
              </View>
              <TouchableOpacity
                onPress={onUpgrade}
                activeOpacity={0.85}
                style={{backgroundColor: C.accent, borderRadius: 10, paddingVertical: 13, alignItems: 'center'}}>
                <Text style={{color: '#000', fontWeight: '800', fontSize: 14}}>Start 7-Day Free Trial</Text>
              </TouchableOpacity>
              <Text style={{color: C.textDim, fontSize: 10, textAlign: 'center', marginTop: 8}}>
                Then $14.99/mo or $119.99/yr · cancel anytime
              </Text>
            </View>
          </View>
        )}

        {/* Verdict + Jerry read + all analytics — Pro only */}
        {isPro !== false && (<>
        <VerdictCard ctx={ctx} awayTeam={awayTeam} homeTeam={homeTeam} sport={gamesSport} jerrySynthesis={jerrySynthesis} />
        <LosingMarketChips ctx={ctx} />
        <JerryReadSection narrative={jerryNarrative} loading={jerryLoading} synthesis={jerrySynthesis} isPro={isPro} onUpgrade={onUpgrade} />
        {/* 2026-09-16: Weekly-lock explainer sits next to the read it
            explains. NFL locks Thu 8am ET → Mon EOD; NCAAF locks
            Wed 8am ET → Tue EOD. ui_notes table gates copy per sport. */}
        {(gamesSport === 'NFL' || gamesSport === 'NCAAF') && (
          <JerryLockNote sport={gamesSport} />
        )}
        <AlignmentStrip ctx={ctx} />

        {/* 2026-09-01: gate on any predicted-score field. Was rendering
            empty "No score projections available" under the Section title
            on FCS games + sparse UFC / NHL cards. */}
        {showPredictedScore && hasAnyPredictedScore(ctx) && (
          <Section title="Predicted Score" hint="range across models">
            <ScoreRange ctx={ctx} awayTeam={awayTeam} homeTeam={homeTeam} />
          </Section>
        )}

        {showStatProjections && gamesSport === 'MLB' && (
          <Section title="Stat Projections" hint="model-implied · check against your prop lines">
            <StatProjectionsMLB ctx={ctx} />
          </Section>
        )}

        {showMoneyFlow && (
          <Section title="Money Flow" hint="bets vs money · sharps vs public">
            <MoneyFlow ctx={ctx} sport={gamesSport} />
          </Section>
        )}

        {/* 2026-09-17: Line Movement hint clarified from "opening → current" to
            note the source. Line Movement shows the CLOSING CONSENSUS across
            books (ctx.home_ml_close / close_spread / close_total). The HRB
            odds box above shows Hard Rock's LIVE price, which can differ
            (Andy 9/17: NO@BAL card showed HRB -425 vs Line Movement -380 on
            same game — legitimately different books, different prices, but
            users read as inconsistency). Explicit source label makes it
            honest. */}
        {showLineMovement && (
          <Section title="Line Movement" hint="opening → close (consensus)">
            <LineMovementStrip ctx={ctx} historicalOdds={historicalOdds} />
          </Section>
        )}

        {/* 2026-09-01: gate on any lens producing a value. Was showing
            empty "Model Consensus" header on thin UFC/NHL/FCS cards. */}
        {showModelConsensus && hasAnyLensValue(ctx, gamesSport) && (
          // 2026-09-17: hint updated from "margin (H+ / A−)" to reflect
          // mixed tile formats. NFL/NCAAF now show spread projections
          // (v3/v4/panel/sp+/mc) alongside probability tiles (LR) and
          // composite tiles (GOAT). Tap any tile → glossary tooltip.
          <Section title="Model Consensus" hint="each model's read · tap for detail">
            <LensGrid ctx={ctx} gamesSport={gamesSport} />
          </Section>
        )}

        {/* 2026-09-13 Signals row — Andy directive: surface WHICH signals
            fired (EPA gap, cohort match, LR shadow, anchor status, GOAT)
            with tap-to-explain info markers. Sits right under Model
            Consensus so users see the "how" behind the numbers, not just
            the outputs. Silent-hides if no signals materially fire. */}
        {(gamesSport === 'NFL' || gamesSport === 'NCAAF') && (
          <SignalsRow ctx={ctx} gamesSport={gamesSport}
                      cohortTagRecords={cohortTagRecords} />
        )}

        {/* 2026-09-01: gate on non-OC pick presence. Was rendering
            empty "No handicapper picks pulled yet" on most NHL/UFC/
            some NCAAF cards. */}
        {showExternalHandicappers && (externalPicks || []).some((p: any) => p.source !== 'oddscrowd') && (
          <Section title="External Handicappers">
            <HandicappersRow picks={externalPicks} homeTeam={homeTeam} awayTeam={awayTeam} sport={gamesSport} records={sourceRecords} />
          </Section>
        )}

        <SportSpecificSlot ctx={ctx} gamesSport={gamesSport} game={game} cohortRecords={cohortTagRecords} />

        {/* 2026-09-01: Recent Schedule card — cross-sport, reads
            team_recent_games matview (populated by refresh_team_recent_games
            RPC called from each pipeline's resolver step). Three tabs:
            away / H2H / home. Silent hide when both teams have zero rows
            + no H2H (pre-season / matview not refreshed). See
            project_rolling_rollup_architecture_901 for the wider
            rollup-tables architecture. */}
        {showRecentSchedule && (
          <Section title="Recent Schedule" hint="last 5 · ATS · O/U">
            <RecentScheduleCard sport={gamesSport} homeTeam={homeTeam} awayTeam={awayTeam} season={ctx?.season} />
          </Section>
        )}

        {/* 2026-09-01: Situational Records — reads team_situational_records
            matview. Sub-tabs Spread/Total/ML × 4 filter rows (Overall,
            L10, Home/Away, Fav/Dog). See RecordPill for the colour rule and
            project_rolling_rollup_architecture_901 for the matview.

            2026-09-25: the hint states the COLOUR RULE, because the recurring
            QA report on this card is "the colours aren't showing." They are
            usually working — the cell just holds a 1-2 game sample, which
            cannot be coloured honestly. Football is current-season only by
            directive (20260916a killed the prior-season blend), so at NCAAF
            week 4 the median filter cell has 3 games and many have 1. Saying
            so turns a grey grid from "broken" into "not enough games yet",
            and it self-resolves as n grows. */}
        {showSituationalRec && (
          <Section title="Situational Records"
                   hint="records × market · color marks a clear edge; thin samples stay neutral">
            <SituationalCard sport={gamesSport} homeTeam={homeTeam} awayTeam={awayTeam} season={ctx?.season}
                             homeML={ctx?.close_home_ml ?? ctx?.home_ml_close} awayML={ctx?.close_away_ml ?? ctx?.away_ml_close} />
          </Section>
        )}

        {/* 2026-09-01: Team Stats — reads team_stats_rolling matview.
            Offense/Defense sub-tabs, each stat row shows raw value +
            rank chip (quintile-colored). NCAAF-only content today; MLB/
            NFL/NBA/NCAAB/NHL follow-up ships. See
            project_rolling_rollup_architecture_901. */}
        {/* 2026-09-24: the hint was hardcoded "ranks are FBS-only" and
            rendered on every sport, so an MLB card claimed its ranks were
            college-football-only. The qualifier is real but it is NCAAF's
            alone. Andy caught it on a Cardinals/Pirates card. */}
        {/* 2026-09-26: the NCAAF suffix claimed "ranks are FBS-only" and that
            is not true — league_size on these rows is 139 for SP+, 216 for
            defensive rates and 266 for offensive ones, against 134 FBS teams.
            Akron reading "Bot 1%" is real, but it is a percentile of a pool
            that includes non-FBS teams. Saying so is better than a legend
            that is wrong, until the pools are actually FBS-filtered.

            2026-09-26 later: the five per-game keys (pass/rush/total yds,
            penalty yds, turnovers) now rank over 133 teams with a verified
            denominator — see recompute_ncaaf_per_game_stats.py + 20260926d.
            The rate and SP+ pools are still mixed, so the caveat stays but
            is now "pool size varies by stat", which is what is actually
            true rather than a blanket non-FBS warning.

            The colour legend also had to change. Colour tracks the absolute
            percentile and the ▲/▼ carries the head-to-head — they were
            split onto separate channels on 09-25 but the legend still
            described the old single-channel rule, so it was telling users
            green meant something it no longer means. */}
        {showTeamStats && (
          <Section title="Team Stats"
                   // 2026-09-26: the caveat was gated to NCAAF, so the same
                   // component shipped two different legends and Andy read it
                   // as drift. Pool size varies by stat in every sport — NFL
                   // offense ranks over 32 while several computed rows rank
                   // over a filtered subset — so the qualifier is universal.
                   hint={'▲ = better matchup side · color = percentile strength · pool size varies by stat'}>
            <TeamStatsCard sport={gamesSport} homeTeam={homeTeam} awayTeam={awayTeam} season={ctx?.season}
                           statsSource={ctx?.stats_source} />
          </Section>
        )}

        {/* 2026-08-23: Public Splits panel — renders ctx.splits_summary
            (populated by splits_v2_pipeline aggregator). Shows sources_present
            + triple_confirmed markets. User feedback: college football game
            detail was missing splits despite backend data landing. */}
        {showPublicSplits && ctx?.splits_summary && (
          <Expander title="Public Splits" badge={splitsBadge(ctx.splits_summary)}>
            <SplitsSummaryPanel summary={ctx.splits_summary} sport={gamesSport} />
          </Expander>
        )}

        {/* 2026-09-01: gate on breakdown presence — was rendering
            "COHORT SIGNALS · no data" on NHL/UFC/thin NCAAB cards. */}
        {safeJSON(ctx?.signal_confluence_breakdown) && (
          <Expander title="Cohort Signals" badge={cohortBadge(ctx)}>
            <CohortsPanel ctx={ctx} cohortRecords={cohortTagRecords} />
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

        {/* 2026-09-07: title changed from "Your Book · Hard Rock Bet" to
            "Sportsbook Odds" + hint noting Hard Rock is the default display
            (odds pulled from public feeds, no affiliate relationship). Prior
            title implied endorsement / affiliation which is not accurate —
            we display Hard Rock's public odds as informational market data
            same as any odds-comparison site. Post-launch (v1.0.1): user
            book-selector setting so DK/FD/BetMGM bettors see their own
            book's line by default. See project_sportsbook_default_ux_907. */}
        {showSportsbookOdds && (
        <Section title="Sportsbook Odds" hint="Hard Rock lines shown · tap to add parlay or log pick · we're not affiliated with any sportsbook">
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
        )}

        {/* 2026-09-01: gate expander — was rendering "0 books" header
            on late-add NCAAF games where odds fetch missed. */}
        {(game.bookmakers || []).length > 0 && (
          <Expander title="All Book Lines" badge={`${(game.bookmakers || []).length} books`}>
            {/* 2026-09-25: pass the ODDS-API team names, not ctx's.
                This panel matches outcome.name against these strings, and
                outcome.name comes from the Odds API as a full team name
                ("Pittsburgh Steelers"). nfl_game_context.home_team stores an
                ABBREVIATION ("PIT"), and ctx wins the `ctx?.home_team ||
                game.home_team` precedence used everywhere else — so on NFL
                and NCAAF the spread and ML lookups never matched and every
                book rendered "—" for both. Totals still rendered because they
                match the literal string "over", not a team name, which is
                exactly the pattern in Andy's screenshot: 21 books, totals on
                every row, spread and ML blank.
                MLB was unaffected because mlb_game_context.home_team is
                already a full name. */}
            <AllBookLinesPanel
              bookmakers={game.bookmakers || []}
              homeTeam={game.home_team || homeTeam}
              awayTeam={game.away_team || awayTeam}
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
        </>)}
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
function VerdictCard({ctx, awayTeam, homeTeam, sport, jerrySynthesis}: any) {
  let play = ctx?.primary_play;
  // 2026-09-03 JERRY FALLBACK (badge audit fix #4).
  // Prior behavior: if primary_play was null → showed "No primary play
  // surfaced for this game." But the LIST card synthesizes a tier chip
  // from jerry_reads when pp is missing (index.tsx:13567-13577). Result:
  // user taps a STRONG chip on the LIST, lands on an EMPTY verdict.
  // Reads as a bug. Fix: mirror the LIST behavior — build a minimal
  // primary_play from jerrySynthesis when pp is missing, so DETAIL
  // shows what LIST already promised.
  if (!play || typeof play !== 'object') {
    if (jerrySynthesis && jerrySynthesis.call_text) {
      const conv = Number(jerrySynthesis.conviction) || 55;
      const jerryTier = conv >= 80 ? 'PRIME' : conv >= 65 ? 'STRONG' : 'LEAN';
      play = {
        type: jerrySynthesis.call_market || 'ml',
        tier: jerryTier,
        side: jerrySynthesis.call_side || 'HOME',
        label: jerrySynthesis.call_text,
        sub: `Jerry read · ${conv}% confidence`,
        conviction: conv,
        _engine: 'jerry_synthesis',
      };
    } else {
      return (
        <View style={styles.verdict}>
          <Text style={styles.verdictNoPlay}>No primary play surfaced for this game.</Text>
        </View>
      );
    }
  }
  // 2026-09-03 REVISED PER USER: every game gets a take shown in game
  // detail — "not picking is wild, feels like there should be something
  // for every game". Sharp Card / Sweat Card stay filtered (PRIME/STRONG
  // only). Game detail = every game, even low-conviction ones.
  //
  // For COVERAGE / PASS / SKIP tiers: render the pick label as usual
  // BUT add a subtle "LOW CONVICTION" chip so users know this isn't
  // card-eligible — it's a "here's our best guess if you're curious"
  // read, not a play we're recommending.
  //
  // Prior TIER=COVERAGE gate REMOVED — was returning null which felt
  // broken to users clicking into a game expecting to see the pipeline's
  // opinion. Better: show + label the confidence honestly.
  const tier = String(play.tier || '').toUpperCase();
  const isLowConviction = tier === 'COVERAGE' || tier === 'PASS' || tier === 'SKIP';
  const label = play.label || '';
  const sub = play.sub || '';
  // 2026-09-03: sport-aware market label — user flagged "RL" showing on
  // football games (badge says RL but reads as MLB Run Line). Football
  // sports say SPREAD, hockey says PUCK LINE, MLB stays RUN LINE.
  const rawType = String(play.type || '').toLowerCase();
  const marketLabel = rawType === 'rl' ? rlLabel(sport).toUpperCase()
                    : rawType === 'ml' ? 'ML'
                    : rawType === 'total' ? 'TOTAL'
                    : rawType.toUpperCase();
  return (
    <View style={styles.verdict}>
      <View style={{flexDirection:'row', alignItems:'center', gap:6, flexWrap:'wrap'}}>
        {marketLabel && (
          <View style={[styles.verdictTierPill, {backgroundColor: C.border + '22', flexDirection:'row', alignItems:'center'}]}>
            <Text style={[styles.verdictTierText, {color: C.textMuted}]}>{marketLabel}</Text>
          </View>
        )}
        {isLowConviction && (
          <View style={[styles.verdictTierPill, {backgroundColor: C.textMuted + '18', borderWidth:1, borderColor: C.textMuted + '55'}]}>
            <Text style={[styles.verdictTierText, {color: C.textMuted}]}>LOW CONVICTION</Text>
          </View>
        )}
      </View>
      {/* 2026-09-07: dim the pick label on LOW-conviction picks so the
          disclaimer wins the visual hierarchy. Prior render kept the
          headline at full bright weight — reader eye went to big label,
          skipped italic disclaimer. Now the label reads as informational
          context, not a headline. */}
      <Text style={[styles.verdictPlay, isLowConviction && {color: C.textDim, fontWeight: '600'}]}>{label}</Text>
      {sub ? <Text style={[styles.verdictWhy, isLowConviction && {color: C.textMuted}]}>{scrubSourceNames(sub)}</Text> : null}
      {isLowConviction && (
        <Text style={[styles.verdictWhy, {color: C.textMuted, marginTop:6, fontSize:11, fontStyle:'italic'}]}>
          Not a recommended play — thin signal support or unplayable price. Shown here for context; The Sharp + Sweat Card only surface actionable picks.
        </Text>
      )}
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
function JerryReadSection({narrative, loading, synthesis, isPro, onUpgrade}: {
  narrative?: string;
  loading?: boolean;
  synthesis?: {call_text?: string; conviction?: number; call_market?: string;
               call_side?: string; generated_at?: string};
  isPro?: boolean;
  onUpgrade?: () => void;
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

  // 2026-09-06 Pro gate. Free users see a teaser (first ~130 chars) with
  // a locked overlay + upgrade CTA. The teaser is intentional value-tease
  // — enough to prove the read exists and is thoughtful, not enough to
  // capture the actual pick reasoning. Pro users see full read as before.
  if (isPro === false) {
    const TEASER_LEN = 130;
    const teaser = clean.slice(0, TEASER_LEN).trimEnd() + '…';
    return (
      <View style={styles.jerrySection}>
        <View style={styles.jerryHeader}>
          <Text style={styles.jerryTitle}>🧠 JERRY'S READ</Text>
        </View>
        <Text style={[styles.jerryBody, {opacity: 0.55}]}>{teaser}</Text>
        <View style={{marginTop: 12, backgroundColor: C.accent + '11', borderRadius: 10, padding: 14, borderWidth: 1, borderColor: C.accent + '44', alignItems: 'center'}}>
          <Text style={{color: C.accent, fontSize: 10, fontWeight: '800', letterSpacing: 1.2, marginBottom: 4}}>🔒 PRO</Text>
          <Text style={{color: C.text, fontSize: 13, fontWeight: '700', marginBottom: 4, textAlign: 'center'}}>Unlock the full analysis</Text>
          <Text style={{color: C.textMuted, fontSize: 11, lineHeight: 16, textAlign: 'center', marginBottom: 12}}>
            Per-game Jerry reads walk through the model call, the counter-argument, and the confluence signals — the full "why" behind every pick.
          </Text>
          <TouchableOpacity
            onPress={onUpgrade}
            activeOpacity={0.85}
            style={{backgroundColor: C.accent, borderRadius: 8, paddingVertical: 10, paddingHorizontal: 20, alignSelf: 'stretch', alignItems: 'center'}}>
            <Text style={{color: '#000', fontWeight: '800', fontSize: 13}}>Start 7-Day Free Trial</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

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
  // ══ 2026-09-27 · ONE LENS IS NOT A CONSENSUS ══
  //
  // Andy, on BAL @ DAL: "'Models 1/1 HOME' in the top summary contradicts
  // 'Model 67% AWAY' and GOAT on BAL."
  //
  // Both were true and they counted different things. This chip comes from
  // align_status_common.compute_lens_ml_from_context, whose lens set is
  // panel / jerry / v3 / v4 / mc / conf — it does NOT include LR or GOAT,
  // the two lenses the card prints most prominently. On that game
  // projected_spread was exactly 0.0 (no side), v4 was dead and the NFL
  // table has no model_pred_spread at all, so a single lens voted and the
  // strip rendered its lone opinion as "1/1 HOME" — the visual grammar of
  // agreement — directly above a grid showing LR and GOAT on BAL.
  //
  // "n/n" only means something when n is a count of things that could have
  // disagreed. Below two lenses there is no agreement to report, so the
  // chip names it as a single read instead of implying unanimity. The
  // deeper fix is one lens set shared by the strip and the consensus grid;
  // this stops the card contradicting itself in the meantime.
  if (ml.lens_count) {
    const side = ml.lens_side === 'H' ? 'HOME' : ml.lens_side === 'A' ? 'AWAY' : '—';
    const lone = (Number(ml.lens_total) || 0) < 2;
    chips.push({
      label: lone ? 'Model (1 lens)' : 'Models',
      value: lone ? `${side} only` : `${ml.lens_count}/${ml.lens_total} ${side}`,
      kind: lone ? 'neutral' : ml.lens_count >= 5 ? 'ok' : 'neutral',
    });
  }
  const overall = align.overall || {};
  const verdictStr = overall.verdict || 'no_data';
  const isDisagree = verdictStr === 'disagreement';

  // 2026-09-26 · ALIGNED WITH EACH OTHER IS NOT ALIGNED WITH THE PICK.
  //
  // Andy, on Oregon State @ UTEP: "Overall ✓ ALIGNED on a card where the
  // pick opposes everything ... the badge certifies alignment on the one
  // screen where the pick is the outlier." Second confirmed instance
  // after Oklahoma @ Georgia, so it is systematic.
  //
  // `overall.aligned` answers "do the signals agree with EACH OTHER" —
  // and on that card they emphatically did: Handicappers 1/1 AWAY,
  // Models 2/2 AWAY, every model margin on Oregon State. The pick was
  // UTEP. The strip rendered that unanimity as a green tick next to a
  // pick it unanimously contradicted, which is worse than showing
  // nothing: it converts the strongest available warning into an
  // endorsement.
  //
  // The chips already carry the side each group landed on, and ctx
  // carries the pick, so the honest verdict is computable right here:
  // consensus is only reassuring when it points the same way we do.
  const _pickSide = String(ctx?.primary_play?.side || '').toUpperCase();
  const _sides = [ml.lens_side, ml.ext_lead, ml.money_side]
    .filter((s: any) => s === 'H' || s === 'A')
    .map((s: any) => (s === 'H' ? 'HOME' : 'AWAY'));
  const _against = _sides.filter((s: string) => s !== _pickSide).length;
  const _withPick = _sides.filter((s: string) => s === _pickSide).length;
  const _pickIsOutlier = (_pickSide === 'HOME' || _pickSide === 'AWAY')
    && _sides.length >= 2 && _against >= 2 && _withPick === 0;

  const isAligned = overall.aligned === true && !_pickIsOutlier;
  chips.push({
    label: 'Overall',
    value: _pickIsOutlier
      ? `⚠ ${_against} AGAINST PICK`
      : isAligned ? '✓ ALIGNED' : isDisagree ? '⚠ DISAGREE' : '—',
    kind: (_pickIsOutlier || isDisagree) ? 'warn' : isAligned ? 'ok' : 'neutral',
  });

  // 2026-09-12 v1.0.1 #12a: backend-driven chips via align.chips_extra[].
  // Server emits {key, label, value, tooltip, kind, priority} objects — the
  // app iterates them and renders alongside hardcoded chips, sorted priority
  // desc. Enables adding new models/signals (GOAT composite, extreme_public
  // fade, sweat_pick badge, future models) WITHOUT an app rebuild. Tap a
  // chip to see its tooltip explanation.
  const chipsExtra: {key: string; label: string; value: string;
                     tooltip?: string; kind: 'ok'|'warn'|'info'|'neutral';
                     priority?: number}[] = Array.isArray(align.chips_extra) ? align.chips_extra : [];
  const chipsExtraSorted = [...chipsExtra].sort(
    (a, b) => (b.priority ?? 0) - (a.priority ?? 0)
  );

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
      {chipsExtraSorted.map((chip, i) => (
        <TouchableOpacity
          key={`extra-${chip.key ?? i}`}
          activeOpacity={chip.tooltip ? 0.7 : 1}
          onPress={() => {
            if (chip.tooltip) {
              Alert.alert(chip.label, chip.tooltip);
            }
          }}
          style={[styles.alignChip, chipStyleFor(chip.kind || 'neutral')]}
        >
          <Text style={styles.alignChipLabel}>{chip.label}</Text>
          <Text style={[styles.alignChipValue, {color: chipTextColorFor(chip.kind || 'neutral')}]}>
            {chip.value}{chip.tooltip ? ' ⓘ' : ''}
          </Text>
        </TouchableOpacity>
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
function MarketRow({closeSpread, closeTotal, homeML, awayML, homeTeam, awayTeam}: any) {
  // 2026-09-26: "Spread 13.5" never said WHICH team was favoured, so the
  // headline market number was unreadable on its own — a user had to scroll
  // to the pick card to find out. The favourite is derived from the cheaper
  // MONEYLINE rather than the sign of close_spread, whose convention differs
  // by sport (project_close_spread_sign_bug_914). Magnitude only, attached
  // to the favourite, so no storage convention can flip it.
  const _h = Number(homeML), _a = Number(awayML);
  const _mag = Math.abs(Number(closeSpread));
  const favAbbrev = (isFinite(_h) && isFinite(_a) && homeTeam && awayTeam)
    ? abbrev3(_h < _a ? homeTeam : awayTeam) : null;
  const spreadText = (favAbbrev && isFinite(_mag))
    ? `${favAbbrev} -${_mag % 1 === 0 ? _mag.toFixed(0) : _mag.toFixed(1)}`
    : f(closeSpread, 1);
  return (
    <View style={styles.marketRow}>
      <Text style={styles.marketItem}>Spread <Text style={styles.marketVal}>{spreadText}</Text></Text>
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
  let preds: {name: string; a: number; h: number}[] = [];
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
  // 2026-09-16: v4 lens with fallback. MLB writes model_pred_spread /
  // model_pred_total; NFL + NCAAF write v4_spread / v4_total. Prior
  // MLB-only column read silently omitted the v4 lens from
  // Predicted Score on NFL/NCAAF cards — Andy noted these are missing.
  addPred('v4',    ctx?.model_pred_total ?? ctx?.v4_total,
                    ctx?.model_pred_spread ?? ctx?.v4_spread);
  addPred('MC',    mc.mc_expected_total ?? mc.mc_mean_total, mc.mc_expected_margin);
  // 2026-08-25 — cross-sport predicted-score fields so this component
  // renders for NCAAF / NFL / NBA / NCAAB, not just MLB. Each sport's
  // context builder writes its own naming; we probe all of them.
  addPredHA('Model',    ctx?.model_pred_home_points, ctx?.model_pred_away_points);
  addPredHA('SP+',      ctx?.sp_plus_pred_home_pts,   ctx?.sp_plus_pred_away_pts);
  addPredHA('Efficiency', ctx?.eff_pred_home_pts,     ctx?.eff_pred_away_pts);
  // NBA/NHL Elo — if ctx exposes projected points from elo, use those too.
  addPredHA('Elo',      ctx?.elo_pred_home_pts,       ctx?.elo_pred_away_pts);

  // 2026-09-03: LR (supervised logistic regression) as its own lens. The
  // LR predictor writes p_home_win into primary_play._lr_p_home_win when
  // it runs (MLB/NFL/NCAAF). Convert probability to a spread proxy using
  // a rough calibration (~5.5pt spread ≈ 60% p_home_win in NCAAF/NFL,
  // ~1.5 runs ≈ 60% in MLB) and pair with the closing total so LR shows
  // up next to Jerry/v4/MC as a distinct predictor row. Not a full
  // score projection — LR only outputs a win probability — but exposes
  // its verdict as a comparable lens instead of hiding inside pp.sub.
  const lrP = (ctx?.primary_play as any)?._lr_p_home_win;
  const lineTotal = ctx?.close_total;
  if (lrP != null && lineTotal != null && Number.isFinite(parseFloat(String(lrP)))) {
    const p = parseFloat(String(lrP));
    const t = parseFloat(String(lineTotal));
    // Sport-aware spread proxy from p_home_win.
    // MLB: ~1.5 runs per 10% edge; NCAAF/NFL: ~5.5 pts per 10% edge; NBA: ~4.
    const sport = String((ctx?.sport ?? '')).toUpperCase();
    const perPt = sport === 'MLB' ? 15 : (sport === 'NBA' || sport === 'NCAAB' ? 40 : 55);
    const margin = ((p - 0.5) * perPt) / 10; // home minus away
    preds.push({name: 'LR', a: (t - margin) / 2, h: (t + margin) / 2});
  }

  // 2026-09-01: reviewer safety — was rendering "No score projections
  // available" text inside the Predicted Score Section. Parent now
  // gates via hasAnyPredictedScore(). This is defense-in-depth.
  if (preds.length === 0) return null;

  // ══ 2026-09-26 · COLLAPSE LENSES THAT ARE THE SAME NUMBERS TWICE ══
  // Verified on UNLV @ Akron: every SP+ field in ncaaf_game_context is
  // byte-identical to the model field it sits beside —
  //   projected_spread -12.07 == sp_plus_pred_spread -12.07
  //   projected_total   52.21 == sp_plus_pred_total   52.21
  //   model_pred_away_points 29.5 == sp_plus_pred_away_pts 29.5
  // so SP+ is not an independent lens, it is V3 relabelled. Already
  // recorded on 2026-09-20 (project_ncaaf_duplicate_total_lens_920) and
  // never acted on.
  //
  // Counting it twice inflated the denominator AND the tally: the card read
  // "3 of 4 lenses lean UNDER" when only two lenses carry a total at all
  // (V3 under 52.21, MC over 53.6) — a 1-1 split reported as 3-1. Deduping
  // by value fixes the count and the range together, and it self-heals: if
  // SP+ ever becomes genuinely independent, it stops collapsing on its own.
  const _seen = new Set<string>();
  const uniqPreds = preds.filter(p => {
    const k = `${p.a.toFixed(2)}|${p.h.toFixed(2)}`;
    if (_seen.has(k)) return false;
    _seen.add(k);
    return true;
  });
  preds = uniqPreds;

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
        {line != null ? ` · Line ${f(line, 1)} → ` : ''}
        {/* 2026-09-07: was "N/N models agree" which read like "all models
            counted agree" when the denominator was actually just the count
            of NON-NULL lenses. Say "N of N lenses" so the reader
            understands the denominator excludes empty lenses (V4 dashed,
            etc.). If it's split, show the split. */}
        {/* 2026-09-26 · A WIDE RANGE IS DISAGREEMENT, NOT A MAJORITY.
            Andy: "Total range 31.7–43.0 · Line 42.5 → 2 of 3 lenses lean
            OVER. The range midpoint is 37.4 — five points below the line.
            Only the extreme top of the range clears 42.5 ... The range
            itself is 11.3 points wide, which is enormous for a consensus."

            The tally is arithmetically fine — two lenses can both sit near
            43.0 without being deduped — but reporting a majority when the
            lenses span 11 points tells the reader the models agree when
            they emphatically do not, and the midpoint sits on the other
            side of the line from the stated lean. When the spread of lens
            totals is wider than the distance that would flip the call,
            say "split" and show the midpoint, which is the number that
            actually summarises them. */}
        {line != null && overCount + underCount > 0 && (() => {
          const spread = totMax - totMin;
          const mid = (totMax + totMin) / 2;
          const wide = spread >= 6 && Math.abs(mid - Number(line)) >= 2;
          if (wide) {
            return `lenses split ${spread.toFixed(1)} pts wide · midpoint ${mid.toFixed(1)} → `;
          }
          return overCount === preds.length
            ? `all ${preds.length} lens${preds.length === 1 ? '' : 'es'} lean `
            : underCount === preds.length
            ? `all ${preds.length} lens${preds.length === 1 ? '' : 'es'} lean `
            : `${Math.max(overCount, underCount)} of ${overCount + underCount} lenses lean `;
        })()}
        {line != null && (() => {
          const spread = totMax - totMin;
          const mid = (totMax + totMin) / 2;
          const wide = spread >= 6 && Math.abs(mid - Number(line)) >= 2;
          // With a wide split the honest direction is the one the MIDPOINT
          // implies, not the one a bare head-count implies.
          const dir = wide ? (mid > Number(line) ? 'OVER' : 'UNDER') : totalDir;
          return <Text style={{color: dir === 'OVER' ? C.accent : C.sharp, fontWeight: '700'}}>{dir}</Text>;
        })()}
      </Text>
      {jerry ? (
        <View style={styles.jerryBanner}>
          <Text style={styles.jerryLabel}>Top lens (Jerry):</Text>
          <Text style={styles.jerryValue}>
            {abbrev3(awayTeam)} {f(jerry.a, 1)} — {f(jerry.h, 1)} {abbrev3(homeTeam)}
          </Text>
        </View>
      ) : null}
      {/* 2026-09-03: info tap for LR — new predictor lens users won't recognize.
          Only surfaces when LR is actually present in the predictor list. */}
      {preds.some(p => p.name === 'LR') ? (
        <TouchableOpacity
          onPress={() => Alert.alert(
            'LR = Logistic Regression',
            "A supervised model trained on thousands of resolved games. Learns which features actually predict wins (market lines, pitcher stats, recent form, sharp $ flow, etc.) instead of hand-tuned weights.\n\nThe LR runs AFTER the ensemble scorer and can: kill picks it sees as coin flips, replace picks when it strongly disagrees, or confirm the ensemble's call. It's why some picks show 'NO PLAY · coin flip' — the LR overrode a legacy pick it didn't believe.\n\nLive on: MLB (ML + total + prop), NFL (ML), NCAAF (ML + total). Retrained every Monday on fresh outcomes."
          )}
          style={{flexDirection:'row', alignItems:'center', gap:6, marginTop:8, alignSelf:'flex-start'}}
          activeOpacity={0.7}
        >
          <View style={{
            paddingHorizontal:7, paddingVertical:2, borderRadius:10,
            borderWidth:1, borderColor: C.border,
          }}>
            <Text style={{color: C.textMuted, fontSize:11, fontWeight:'700', letterSpacing:0.4}}>
              LR ⓘ  what's this?
            </Text>
          </View>
        </TouchableOpacity>
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
function _sideFromAgg(agg: any): {money: number; bets: number; div: number; sources: number} | null {
  if (!agg || typeof agg !== 'object') return null;
  // 2026-09-07 ROOT-CAUSE FIX: different sources normalize to different metric
  // names in splits_v2_pipeline. OC → money_pct_avg. FR + CZ → handle_pct_avg.
  // Semantically identical (sharp money handle share). Prior code only read
  // money_pct_avg → for any game with cz/fr-only coverage (all NCAAF today,
  // most NHL), money rendered as 0% and MoneyFlow chips were nonsense.
  // Prefer money_pct_avg when present, fall back to handle_pct_avg.
  const money = typeof agg.money_pct_avg === 'number' ? agg.money_pct_avg
              : typeof agg.handle_pct_avg === 'number' ? agg.handle_pct_avg
              : null;
  const bets  = typeof agg.bets_pct_avg  === 'number' ? agg.bets_pct_avg  : null;
  if (money == null && bets == null) return null;
  // ══ 2026-09-26 · DIVERGENCE IS DERIVED, NEVER READ ══
  // This used to prefer a stored divergence_avg and only compute the
  // difference as a fallback. divergence_avg is the AVERAGE OF PER-SOURCE
  // DIFFERENCES, while the bars above it show the AVERAGE MONEY and the
  // AVERAGE BETS. Those are not the same quantity whenever the sources
  // cover different books — average-of-differences ≠ difference-of-averages
  // — so the header contradicted the two bars directly beneath it:
  //
  //   moneyline  67% money / 86.5% bets  -> real -19.5pp, displayed +24pp
  //   spread     76% money / 46%   bets  -> real +30pp,   displayed +52pp
  //   total      53% money / 66.7% bets  -> real -13.7pp, displayed  +0pp
  //
  // Any user can do this subtraction, so a stored number that disagrees
  // with the bars is worse than no number. Derive it from exactly what is
  // rendered; if the aggregate wants a different figure it has to change
  // the bars too.
  const div = (money != null && bets != null) ? Math.round(money - bets) : null;
  // 2026-09-07: propagate `sources_agree` down so MoneyMarket can
  // downgrade the SHARP/STEAM label when only one source is reporting.
  // User audit found FSU game showed "SHARP · OVER 99% money" from a
  // single-source (cleatz only) reading — no cross-validation, but the
  // UI presented it with the same confidence as multi-source truth.
  const sources = typeof agg.sources_agree === 'number' ? agg.sources_agree : 0;
  return {money: money ?? 0, bets: bets ?? 0, div: div ?? 0, sources};
}
function _marketFromSummary(mktObj: any): any | null {
  if (!mktObj || typeof mktObj !== 'object') return null;
  const sides = ['HOME', 'AWAY', 'OVER', 'UNDER'];
  let best: {side: string; money: number; bets: number; div: number; sources: number} | null = null;
  for (const s of sides) {
    if (!(s in mktObj)) continue;
    const agg = _sideFromAgg(mktObj[s]);
    if (!agg) continue;
    if (!best || agg.money > best.money) {
      best = {side: s, ...agg};
    }
  }
  if (!best) return null;
  return {pick: best.side, money: best.money, bets: best.bets, div: best.div, sources: best.sources};
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
  // 2026-09-26: STEAM claims a reverse LINE MOVE, so it needs to know
  // whether the line actually moved. Same open/close fields the Line
  // Movement strip below renders, so the two sections cannot contradict
  // each other — which is exactly what "STEAM +52pp" over "13.5 -> 13.5
  // flat" was doing. undefined (no opener on file) is NOT treated as
  // moved: unknown must not license the loudest badge we have.
  const _moved = (open: any, close: any): boolean | undefined => {
    if (open == null || close == null) return undefined;
    const a = Number(open), b = Number(close);
    if (!isFinite(a) || !isFinite(b)) return undefined;
    return Math.abs(a - b) >= 0.5;
  };
  const movedSpread = _moved(ctx?.open_spread, ctx?.close_spread);
  const movedTotal  = _moved(ctx?.open_total,  ctx?.close_total);
  const movedML     = _moved(ctx?.home_ml_open, ctx?.close_home_ml);

  // ══ 2026-09-27 · MONEY SHARE IS INFLATED BY PRICE, NOT ONLY BY SHARPS ══
  //
  // Andy: "Money flow tagging a -203 favorite as SHARP isn't reliable.
  // Favorites naturally draw more money than tickets because bettors have
  // to stake more to win the same amount. Consider normalizing for price,
  // or suppressing the tag on heavy favorites."
  //
  // He is right and the size of it is startling. On BAL @ DAL the card
  // showed "+24pp sharp divergence · money 87% vs tickets 63.5% on AWAY".
  // BAL was -203 and DAL +153, so a bettor sizing to win the same amount
  // stakes 2.03 units on BAL against 0.65 on DAL. Feed 63.5% of TICKETS
  // through those stakes and you get ~84% of the MONEY with not one sharp
  // dollar involved. Against that baseline the real excess is ~3pp, not
  // 24 — and the badge was reading a pricing identity as a signal.
  //
  // Only the moneyline is passed prices: spread and total sit near -110
  // on both sides, where the effect is under a point and the raw
  // divergence is already honest.
  const _mlPickIsHome = String(src.ml?.pick || '').toUpperCase().includes('HOME');
  const markets: {key: 'ml'|'rl'|'total'; label: string; data: any;
                  moved: boolean | undefined; pickPrice?: any; oppPrice?: any}[] = ([
    {key: 'ml' as const, label: 'Moneyline', data: src.ml, moved: movedML,
     pickPrice: _mlPickIsHome ? ctx?.close_home_ml : ctx?.close_away_ml,
     oppPrice:  _mlPickIsHome ? ctx?.close_away_ml : ctx?.close_home_ml},
    {key: 'rl' as const, label: rlLabel(sport), data: src.rl, moved: movedSpread},
    {key: 'total' as const, label: 'Total', data: src.total, moved: movedTotal},
  ]).filter(x => x.data);
  return (
    <View style={{gap: 8}}>
      {markets.map(m => (
        <MoneyMarket key={m.key} label={m.label} data={m.data} lineMoved={m.moved}
                     pickPrice={m.pickPrice} oppPrice={m.oppPrice} />
      ))}
    </View>
  );
}

// Stake a bettor must lay to win one unit at this American price. This is
// the whole mechanism behind price-inflated money share: at -203 you put up
// 2.03 to win 1; at +153 you put up 0.65.
function _stakePerUnitWon(price: any): number | null {
  const p = Number(price);
  if (!isFinite(p) || p === 0) return null;
  return p < 0 ? Math.abs(p) / 100 : 100 / p;
}

// Money share you would expect from PRICE ALONE, given the ticket split —
// i.e. with no sharp money in the market at all. Returns null when either
// price is missing, so an unknown price can never manufacture a baseline.
function _expectedMoneyPct(betsPct: number, pickPrice: any, oppPrice: any): number | null {
  const s1 = _stakePerUnitWon(pickPrice);
  const s2 = _stakePerUnitWon(oppPrice);
  if (s1 == null || s2 == null) return null;
  const t = Math.max(0, Math.min(1, betsPct / 100));
  const denom = t * s1 + (1 - t) * s2;
  if (denom <= 0) return null;
  return 100 * (t * s1) / denom;
}

function MoneyMarket({label, data, lineMoved, pickPrice, oppPrice}: any) {
  if (!data) return null;
  const rawDiv = data.div ?? 0;
  const money = Math.max(0, Math.min(100, data.money ?? 0));
  const bets = Math.max(0, Math.min(100, data.bets ?? 0));
  // Divergence measured against what the PRICE already explains. Falls back
  // to the raw gap when prices are unavailable, so markets without odds
  // behave exactly as before rather than losing their badge.
  const expMoney = _expectedMoneyPct(bets, pickPrice, oppPrice);
  const div = expMoney == null ? rawDiv : Math.round(money - expMoney);
  const sources = typeof data.sources === 'number' ? data.sources : 0;
  // 2026-09-02: sharp threshold aligned with pipeline (divergence_threshold=20
  // OR money>=60). 2026-09-07 ROOT-CAUSE FIX: also require sources_agree >= 2
  // before applying any sharp label. User audit on FSU@SMU showed the app
  // labeling "OVER SHARP · 99% money" from a single-source (cleatz only)
  // reading — extreme cleatz-only numbers rendered with the same confidence
  // as cross-validated multi-source truth. Fix: single-source data still
  // renders bars + percentages, but the SHARP/STEAM chip is gated on
  // sources_agree >= 2 so users only see confident signaling when we have
  // cross-source confirmation. Blast radius: every NCAAF total (thin
  // source coverage) + any market where OC/FR haven't reported yet.
  const multiSource = sources >= 2;
  // 2026-09-16: SHARP chip previously fired on money>=60 alone, even
  // when divergence was 0pp (81% money / 81% bets = public consensus,
  // NOT sharp). Andy screenshot: OVER SHARP with +0pp divergence.
  // Fix: require actual divergence >= 5pp before tagging SHARP.
  // multiSource + money>=60 alone is downgraded to consensus, not
  // sharp. Extreme still requires large money-vs-bets gap.
  // ══ 2026-09-26 · SIGN MATTERS. abs() WAS CALLING THE PUBLIC SIDE SHARP ══
  // Sharp action means the MONEY share exceeds the TICKET share: few bets
  // carrying big handle. div < 0 is the opposite — lots of tickets, little
  // money — which is the retail pattern. Gating on Math.abs() meant the
  // moneyline row rendered "SHARP · money loading on AWAY while public sits
  // out" on 67% money against 86.5% of tickets. 86.5% of tickets IS the
  // public, and they were not sitting out. Confirmed by Andy in two sports.
  // ══ 2026-09-26 · A ONE-SIDED MARKET HAS NOTHING TO DIVERGE FROM ══
  // Andy, third card in a row: "95% money badged SHARP ... at that level
  // there's no two-sided market to diverge from" (ORST 99%, MIA@CHC 99%,
  // CAR@CLE 95%). He is right — when 95% of the handle and 90% of the
  // tickets are on one side, everyone is on that side and the remaining
  // gap is noise on a tiny base, not sharp money picking a side. Badging
  // it tells the user that a near-unanimous market is a signal.
  const oneSided = money >= 92 || bets >= 92;
  const sharp = multiSource && !oneSided && div >= 5 && (div >= 20 || money >= 60);

  // ══ 2026-09-26 · THE PUBLIC-HEAVY SIDE IS A SIGNAL TOO ══
  // Andy: "the largest divergence on the card is unbadged. SPREAD AWAY
  // -28pp — money 52%, bets 80.5% ... rendered as a plain gray row with
  // no explanatory copy. Meanwhile +10pp on the moneyline gets a SHARP
  // badge and a full sentence. The badge threshold only looks at positive
  // divergence."
  //
  // Correct, and it was my own doing: when I fixed abs() calling the
  // public side SHARP, I gated on div >= 5 and left the negative case
  // rendering nothing at all. But heavy tickets with light money is the
  // textbook fade pattern — the strongest thing on that card — and it was
  // the one row with no label. It gets its own badge rather than being
  // folded into SHARP, because it means the opposite thing.
  const publicHeavy = multiSource && !oneSided && div <= -15;
  // STEAM additionally requires the LINE TO HAVE MOVED. "Massive reverse-
  // line signal" on SPREAD 13.5 -> 13.5 flat is a claim about a move that
  // did not happen — a reverse line move is by definition the line going
  // against the money. This is the loudest badge in the app and it was
  // firing on nothing.
  const extremeSharp = multiSource && lineMoved === true
                       && (div >= 50 || (money >= 80 && bets <= 30));
  return (
    <View style={[
      styles.moneyMarket,
      sharp && {borderLeftColor: C.sharp, backgroundColor: C.sharpDim},
    ]}>
      <View style={styles.moneyMarketHeader}>
        <Text style={styles.moneyMarketLabel}>{label}</Text>
        <View style={{flexDirection: 'row', alignItems: 'center', gap: 6}}>
          <Text style={styles.moneyMarketSide}>{data.pick || '—'}</Text>
          {extremeSharp ? (
            <View style={[styles.sharpBadge, {backgroundColor: C.sharp}]}>
              <Text style={[styles.sharpBadgeText, {color: '#000'}]}>STEAM</Text>
            </View>
          ) : sharp ? (
            <View style={styles.sharpBadge}>
              <Text style={styles.sharpBadgeText}>SHARP</Text>
            </View>
          ) : publicHeavy ? (
            <View style={[styles.sharpBadge, {backgroundColor: C.warn}]}>
              <Text style={[styles.sharpBadgeText, {color: '#000'}]}>PUBLIC</Text>
            </View>
          ) : sources === 1 ? (
            <View style={[styles.sharpBadge, {backgroundColor: 'transparent', borderWidth: 1, borderColor: C.textMuted}]}>
              <Text style={[styles.sharpBadgeText, {color: C.textMuted}]}>1 SRC</Text>
            </View>
          ) : null}
          <Text style={styles.moneyMarketDiv}>{div >= 0 ? `+${div}` : div}pp</Text>
        </View>
      </View>
      <View style={{gap: 5}}>
        <MoneyBar label="Money" pct={money} color={C.sharp} />
        <MoneyBar label="Bets" pct={bets} color={C.warn} />
      </View>
      {extremeSharp && (
        <Text style={styles.moneyDivNote}>
          <Text style={{color: C.sharp, fontWeight: '800'}}>🚨 STEAM — {money}% money vs {bets}% bets on {data.pick}</Text>
          {' · '}massive reverse-line signal, sharps hammering while public backs the other side
        </Text>
      )}
      {publicHeavy && (
        <Text style={styles.moneyDivNote}>
          <Text style={{color: C.warn, fontWeight: '800'}}>{`${bets}% of bets but only ${money}% of the money on ${data.pick || 'this side'}`}</Text>
          {' · '}tickets are piling in without the handle behind them — the public-heavy pattern, and the side to fade rather than follow
        </Text>
      )}
      {/* ══ 2026-09-28 · A PRICE-ADJUSTED NUMBER MUST CARRY ITS EXPLANATION ══
          QA on PHI @ CHI: "the moneyline pp label is still broken. It shows
          86 vs 83 as -8pp when the gap is +3."

          The -8pp is CORRECT and it is mine. PHI closed -205 against CHI
          +170, so a bettor sizing to win the same amount stakes 2.05 on PHI
          against 0.59 on CHI. Feed 83% of TICKETS through those stakes and
          you get ~94% of the MONEY with no sharp action at all. Only 86%
          arrived — money is LIGHTER than the price implies, which is a fade
          signal, not a sharp one. Raw +3 is the misleading number.

          The bug is that the explanation only rendered when the SHARP badge
          fired (div >= 5), so a NEGATIVE adjusted divergence printed a bare
          "-8pp" beside 86% and 83% with nothing to reconcile it. Reading
          that as broken is the correct reaction to what was on screen.

          The note now renders whenever the adjustment was applied, whatever
          the sign. A number the reader cannot reproduce from the two bars
          above it has to say why. */}
      {expMoney != null && !sharp && !extremeSharp && !publicHeavy && (
        <Text style={styles.moneyDivNote}>
          {`money share (${money}%) vs the ${Math.round(expMoney)}% this ticket split (${bets}%) would produce at these prices anyway`}
          {div < 0
            ? ' · handle is LIGHTER than the price implies, which leans against this side'
            : ' · roughly in line once price is accounted for'}
        </Text>
      )}
      {sharp && !extremeSharp && (
        <Text style={styles.moneyDivNote}>
          {/* 2026-09-26: was +{abs(div)}, which printed a plus sign on a
              negative divergence. `sharp` now requires div >= 5 so this is
              genuinely positive, but print the real signed number so the
              header, the bars and this line can never disagree again. */}
          <Text style={{color: C.sharp, fontWeight: '700'}}>+{div}pp sharp divergence</Text>
          {expMoney == null
            ? <>{' · '}money share ({money}%) running ahead of ticket share ({bets}%) on {data.pick}</>
            /* 2026-09-27: state the PRICE-ADJUSTED comparison, because the
               raw one reads as a much bigger signal than it is. At -203,
               63.5% of tickets already produces ~84% of the money with no
               sharp action at all, so quoting "87% money vs 63.5% tickets"
               credited pricing arithmetic to sharps. */
            : <>{' · '}money share ({money}%) vs the {Math.round(expMoney)}% this ticket
               split ({bets}%) would produce at these prices anyway, on {data.pick}</>}
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
  // 2026-09-15: added ctx.home_ml_open fallback. MLB ctx stores opening
  // ML under home_ml_open / away_ml_open, but this component only checked
  // historicalOdds.opening_ml_home (NFL-style key). Andy saw ATH @ TB with
  // "ML (HOME) — → — no open" despite ctx.home_ml_open = -232 being
  // populated.
  // 2026-09-28 · NHL CALLS IT A PUCK LINE.
  // Andy on the first NHL card: "no line movement data". Measured:
  // close_spread and open_spread are populated on 0 of 40 NHL games, while
  // close_puckline / open_puckline are populated on 25. This strip read only
  // the *_spread names, so the Spread row could never render for NHL — and
  // the same omission silently emptied the pick-relative colouring below,
  // which needs openN/currN to compute a delta.
  const openSp = ctx?.open_spread ?? ctx?.open_puckline
    ?? historicalOdds?.opening_spread;
  const openTot = ctx?.open_total ?? historicalOdds?.opening_total;
  const openHomeML = historicalOdds?.opening_ml_home ?? ctx?.home_ml_open;
  const closeSp = ctx?.close_spread ?? ctx?.close_puckline_home ?? ctx?.close_puckline;
  const closeTot = ctx?.close_total;
  const closeHomeML = ctx?.home_ml_close ?? ctx?.close_home_ml;

  // ══ 2026-09-28 · A SPREAD WITHOUT A SIGN OR A TEAM SAYS NOTHING ══
  // Andy's PHI @ CHI QA: the opener rendered a bare "1.5". A spread is a
  // number ABOUT somebody — "1.5" does not say who is giving the points, and
  // the reader cannot recover it from the strip.
  //
  // Reading close_spread's sign would be the obvious fix and it is the one
  // trap this file documents most loudly: the convention is inverted between
  // NFL and NCAAF (project_close_spread_sign_bug_914), so a sign-reading fix
  // is right in one sport and backwards in the other. The market-strip tiles
  // below already solved this — MAGNITUDE from the spread, DIRECTION from the
  // moneyline, because the cheaper price is the favourite in every sport with
  // no convention to get wrong. Same approach here, so the two strips cannot
  // disagree.
  //
  // Both numbers are anchored to the HOME team so the movement is a
  // like-for-like comparison, and the label names that team once. When the
  // prices are missing the direction is genuinely unknown, so it falls back
  // to the bare magnitude rather than guessing a side.
  const _lmHomeML = Number(ctx?.close_home_ml ?? ctx?.home_ml_close);
  const _lmAwayML = Number(ctx?.close_away_ml ?? ctx?.away_ml_close);
  const _lmHomeFav = (isFinite(_lmHomeML) && isFinite(_lmAwayML))
    ? _lmHomeML < _lmAwayML : null;
  const _lmHomeAbbr = abbrev3(ctx?.home_team);
  const _spreadFmt = (v: any) => {
    if (v == null) return '—';
    const n = Number(v);
    if (!isFinite(n)) return String(v);
    if (_lmHomeFav == null) return f(Math.abs(n), 1);
    const signed = _lmHomeFav ? -Math.abs(n) : Math.abs(n);
    return signed > 0 ? `+${f(signed, 1)}` : f(signed, 1);
  };

  const items = [
    // `label` stays 'Spread' — the pick-relative colour logic below keys off
    // it. displayLabel is the rendered caption.
    {label: 'Spread', displayLabel: _lmHomeFav != null && _lmHomeAbbr
       ? `Spread (${_lmHomeAbbr})` : 'Spread',
     open: openSp, current: closeSp, fmt: _spreadFmt},
    {label: 'Total', open: openTot, current: closeTot, fmt: (v: any) => f(v, 1)},
    // 2026-09-26: American odds need their sign. This printed a bare "400"
    // while the MARKET strip on the same sheet showed "+400" for the same
    // price, so one number appeared twice in two formats.
    {label: 'ML (Home)', open: openHomeML, current: closeHomeML,
     fmt: (v: any) => {
       if (v == null) return '—';
       const n = Number(v);
       return isFinite(n) ? (n > 0 ? `+${n}` : String(n)) : String(v);
     }},
  ];

  return (
    <View style={styles.lineMoveStrip}>
      {items.map((it, i) => {
        const openN = typeof it.open === 'number' ? it.open : parseFloat(it.open);
        const currN = typeof it.current === 'number' ? it.current : parseFloat(it.current);
        // 2026-09-07: distinguish "no opening line captured" from "opening
        // matched current" (flat). Prior code rendered 'flat' whenever open
        // was falsy — misleading on NFL preseason games where we didn't
        // pull an opening odds snapshot at all. User audit reproducer:
        // BAL @ IND spread rendered "—→-3.5 flat" when there was no open.
        const openMissing = it.open == null || !isFinite(openN);
        const delta = !openMissing && isFinite(currN) ? currN - openN : null;

        // ══ 2026-09-26 · COLOUR THE MOVE RELATIVE TO OUR PICK ══
        // Andy: "Line-movement color isn't pick-relative. Both tiles
        // render green/↑. The spread moving 10.5 -> 11.5 helps a UTEP
        // backer; the total moving 55.5 -> 56.5 hurts the UNDER that all
        // three lenses lean. Same color, opposite meaning."
        //
        // The colour was reading pure arithmetic — the number went up, so
        // green — which says nothing about whether the move helped the
        // bet on the card above it.
        //
        // "Helps" here means the CURRENT NUMBER IS BETTER THAN THE OPEN
        // FOR OUR SIDE: a dog wants more points, a favourite wants fewer,
        // an UNDER wants a higher total, an OVER a lower one. (Andy's
        // total example used the opposite sense — market sentiment rather
        // than price — and the two readings disagree, so the tile now
        // spells out which one it means instead of relying on colour
        // alone.) When we cannot tell, it stays neutral rather than
        // guessing.
        const _pp = ctx?.primary_play || {};
        const _pt = String(_pp.type || '').toLowerCase();
        const _side = String(_pp.side || '').toUpperCase();
        const _line = Number(_pp.line);
        let favours: 'us' | 'them' | null = null;
        if (delta != null && delta !== 0) {
          if (it.label === 'Total' && (_pt === 'total' || _side === 'OVER' || _side === 'UNDER')) {
            const wantsUnder = _side === 'UNDER'
              || String(_pp.label || '').toLowerCase().includes('under');
            favours = (wantsUnder ? delta > 0 : delta < 0) ? 'us' : 'them';
          } else if (it.label === 'Spread' && (_pt === 'rl' || _pt === 'spread')
                     && isFinite(_line)) {
            // A positive pick line means we are taking points.
            const takingPoints = _line > 0;
            // `delta` is on close_spread, whose sign convention differs by
            // sport, so compare MAGNITUDE of the number our side lays or
            // receives instead — that is convention-independent.
            const openMag = Math.abs(openN), currMag = Math.abs(currN);
            if (currMag !== openMag) {
              favours = (takingPoints ? currMag > openMag : currMag < openMag)
                ? 'us' : 'them';
            }
          }
        }
        const deltaColor = openMissing ? C.textDim
                          : delta == null ? C.textDim
                          : delta === 0 ? C.textDim
                          : favours === 'us' ? C.accent
                          : favours === 'them' ? C.fade
                          : C.textDim;
        return (
          <View key={i} style={styles.lineMoveItem}>
            <Text style={styles.lineMoveLabel} numberOfLines={1}>
              {(it as any).displayLabel ?? it.label}
            </Text>
            <Text style={styles.lineMoveValues}>
              <Text style={{color: C.textMuted}}>{it.fmt(it.open)}</Text>
              <Text style={{color: C.textDim, fontSize: 10}}>  →  </Text>
              <Text style={{color: C.text, fontWeight: '700'}}>{it.fmt(it.current)}</Text>
            </Text>
            <Text style={[styles.lineMoveDelta, {color: deltaColor}]}>
              {openMissing ? 'no open' : delta == null ? '—' : delta === 0 ? 'flat' : delta > 0 ? `↑ +${delta.toFixed(1)}` : `↓ ${delta.toFixed(1)}`}
            </Text>
            {/* Colour alone cannot distinguish "the number went up" from
                "this move helped our bet", so the tile says which. */}
            {favours && (
              <Text style={{color: favours === 'us' ? C.accent : C.fade,
                            fontSize: 8, letterSpacing: 0.3}}>
                {favours === 'us' ? 'better for our pick' : 'worse for our pick'}
              </Text>
            )}
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
       ctx?.v4_spread ?? ctx?.model_pred_spread,
       ctx?.v4_total  ?? ctx?.model_pred_total,
       mc.mc_expected_margin, mc.mc_expected_total, mc.mc_mean_total,
       ctx?.signal_confluence_net]
    : [ctx?.projected_spread, ctx?.projected_total,
       ctx?.v4_spread ?? ctx?.model_pred_spread,
       ctx?.v4_total  ?? ctx?.model_pred_total,
       ctx?.signal_confluence_net];
  return candidates.some(v => v != null);
}

function LensGrid({ctx, gamesSport}: any) {
  // Lenses deliberately omitted this card, with the reason. Rendered as a
  // footnote so the tile count is never silently different between games.
  const _hiddenLenses: string[] = [];
  const mc = safeJSON(ctx?.mc_probabilities) || {};
  // 2026-09-01: NCAAF gets MC lens too — mirrors NFL/MLB. Simulator
  // populates mc_probabilities via mlb_pipeline/ncaaf_mc_simulator.py
  // (schema in 20260901h_ncaaf_mc_column.sql, workflow step in
  // .github/workflows/ncaaf_pipeline.yml after game_context build).
  // Same lens chip shape so cross-sport rendering stays uniform.
  let rows = gamesSport === 'MLB' ? [
    {name: 'Panel', m: ctx?.panel_implied_margin, t: ctx?.panel_implied_total},
    {name: 'Jerry', m: ctx?.jerry_pred_spread, t: ctx?.jerry_pred_total},
    {name: 'v3', m: ctx?.projected_spread, t: ctx?.projected_total},
    {name: 'v4', m: ctx?.model_pred_spread, t: ctx?.model_pred_total},
    {name: 'MC', m: mc.mc_expected_margin, t: mc.mc_expected_total ?? mc.mc_mean_total},
  ] : gamesSport === 'NCAAF' ? (() => {
    // 2026-09-17: pull LR shadow from primary_play._lr_ml_shadow. LR is
    // a probability (p_home_win), not a spread — render as "H XX%" /
    // "A XX%" via displayMargin override so the tile format stays
    // consistent with the rest of the grid (sign color still driven
    // by whether p_home is above/below 0.5).
    const pp = ctx?.primary_play || {};
    const lrP = Number(pp?._lr_ml_shadow?.p_home_win);
    const lrTile = isFinite(lrP) ? {
      name: 'LR',
      m: (lrP - 0.5) * 10,          // sign-only proxy for color; not shown
      t: null,
      displayMargin: lrP >= 0.5
        ? `H ${Math.round(lrP * 100)}%`
        : `A ${Math.round((1 - lrP) * 100)}%`,
    } : null;
    // 2026-09-17: NCAAF ctx doesn't have v4_spread/v4_total or
    // model_pred_spread/model_pred_total columns — only per-team point
    // predictions (model_pred_home_points / model_pred_away_points).
    // Compute the v4 margin + total from those instead of falling
    // through to null. Andy caught Syracuse @ Pitt rendering v4 as "—"
    // while the DB had valid predictions.
    const _v4h = Number(ctx?.model_pred_home_points);
    const _v4a = Number(ctx?.model_pred_away_points);
    const _v4Margin = (isFinite(_v4h) && isFinite(_v4a))
      ? (_v4h - _v4a)
      : (ctx?.v4_spread ?? ctx?.model_pred_spread);
    const _v4Total = (isFinite(_v4h) && isFinite(_v4a))
      ? (_v4h + _v4a)
      : (ctx?.v4_total ?? ctx?.model_pred_total);
    // 2026-09-20 — the TOTALS column used to lie. v3/v4/SP+ all rendered
    // the same number as three independent lenses: ncaaf_game_context
    // assigns projected_total, sp_plus_pred_total and the v4 per-team
    // points from ONE SP+ computation (v3 == SP+ on 72/72; v4 differs only
    // by a rounding artifact, 54.06 vs 54.10). Three tiles agreeing looked
    // like corroboration when it was one model shown three times.
    //
    // The MARGINS are genuinely independent (Vanderbilt @ Auburn: v3 6.15,
    // v4 -2.10, SP+ -0.90) so every tile keeps its margin. Only the
    // duplicated totals are dropped — v3 carries the shared SP+ total and
    // MC carries the one real second opinion (0/68 identical, mean |diff|
    // 1.13).
    // ══ 2026-09-26 · SP+ IS NOT A SECOND OPINION ON THE MARGIN EITHER ══
    // The note above kept the SP+ MARGIN on the grounds that margins are
    // "genuinely independent", citing one game. Measured across the whole
    // board on 2026-09-26: 88 of 88 NCAAF games have BYTE-IDENTICAL v3 and
    // SP+ margins, mean |difference| 0.000, max 0.00. It is v3 relabelled
    // for the margin exactly as it already was for the total.
    //
    // Andy has now flagged it three times ("V3 and SP+ identical for the
    // third time ... the SP+ tile is mirroring V3") and the underlying
    // finding has been open since project_ncaaf_duplicate_total_lens_920
    // on 09-20. Showing it twice makes MODEL CONSENSUS read as six
    // independent lenses when it is five, and manufactures corroboration
    // on exactly the screen a user checks for corroboration.
    //
    // Dropped only WHEN IT DUPLICATES, so this self-heals: if SP+ ever
    // becomes a real second model the tile returns on its own.
    const _v3m = ctx?.projected_spread;
    const _spm = ctx?.sp_plus_pred_spread;
    const _spDupe = (_v3m != null && _spm != null
                     && Math.abs(Number(_v3m) - Number(_spm)) < 0.005);
    return [
      {name: 'v3', m: _v3m, t: ctx?.projected_total},
      {name: 'v4', m: _v4Margin, t: null},
      ...(_spDupe ? [] : [{name: 'SP+', m: _spm, t: null}]),
      {name: 'MC',  m: mc.mc_expected_margin, t: mc.mc_expected_total ?? mc.mc_mean_total},
      ...(lrTile ? [lrTile] : []),
      {name: 'Conf', m: ctx?.signal_confluence_net, t: null},
    ];
  })() : gamesSport === 'NFL' ? (() => {
    // 2026-09-17 v2: full model surface. Was 3 tiles (v3/v4/conf), then
    // added Panel (4 tiles), now LR + GOAT round out to 6 lenses. Andy
    // 9/17: "I want all models for NFL and NCAAF queued and surfaced."
    // LR + LOGREG are near-identical (NYG@LA: 0.7222 vs 0.7239) so
    // showing both is noise — dropped LOGREG, kept LR as the shared
    // ML probability read.
    const pp = ctx?.primary_play || {};
    const lrP = Number(pp?._lr_ml_shadow?.p_home_win);
    const lrTile = isFinite(lrP) ? {
      name: 'LR',
      m: (lrP - 0.5) * 10,
      t: null,
      displayMargin: lrP >= 0.5
        ? `H ${Math.round(lrP * 100)}%`
        : `A ${Math.round((1 - lrP) * 100)}%`,
    } : null;
    // GOAT chip is parsed from _goat_shadow.chip.value ("TEAM · TIER").
    const goatVal = String(pp?._goat_shadow?.chip?.value || '').trim();
    let goatTile: any = null;
    if (goatVal && goatVal !== 'PASS') {
      const goatTeam = goatVal.split('·')[0].trim();
      const goatTier = (goatVal.split('·')[1] || '').trim();
      const home = String(ctx?.home_team || '');
      const isHomeLean = goatTeam && (home.includes(goatTeam) || goatTeam.includes(home));
      goatTile = {
        name: 'GOAT',
        m: isHomeLean ? 1 : -1,      // sign-only for color
        t: null,
        displayMargin: `${goatTeam}${goatTier ? ` · ${goatTier}` : ''}`,
        // ══ 2026-09-28 · "STRO/NG" ══
        // Andy's PHI @ CHI QA: the GOAT tile broke mid-word. The lens row
        // lays up to seven tiles at flex:1, so each gets roughly 38px of
        // content width, and "STRONG" at 12px bold is about 46px. The WORD
        // is wider than the tile, so there is no whitespace for the wrap to
        // land on and RN breaks inside it. Every other tile is a short
        // "DAL 5.7"; this is the only two-word value in the row, so the
        // smaller face applies here rather than shrinking the whole row.
        smallText: true,
      };
    }
    // ══ 2026-09-26 · AN EMPTY LENS IS NOT A LENS ══
    // Andy: "V4 is completely empty — first card where it has no margin
    // and no total at all." Measured: nfl_game_context.v4_spread is
    // populated 0 of 239 rows, as are v4_total, v4_confidence and
    // v4_features_used, and the fallback field model_pred_spread does not
    // exist on the NFL table at all. So the NFL V4 tile has never had
    // anything to show and renders a dead slot in a row the user reads as
    // "each model's read".
    //
    // A model with no output should be absent, not present-and-blank —
    // a blank tile reads as "this model has no opinion", which is a
    // different and much stronger claim than "this model did not run".
    // Computed from the same fields the tile uses, so it returns the
    // moment the v4 columns start being populated.
    const _v4m = ctx?.v4_spread ?? ctx?.model_pred_spread;
    const _v4t = ctx?.v4_total ?? ctx?.model_pred_total;
    const _v4Live = _v4m != null || _v4t != null;
    // 2026-09-26: Andy — "V4 is gone entirely ... if it's conditional,
    // the card should say so rather than silently changing the lens
    // count." Fair: hiding a dead tile is right, doing it invisibly is
    // not, because the reader counts the tiles to judge how many
    // independent opinions back the pick. Recorded so the grid can say
    // which lens is absent and why.
    if (!_v4Live) _hiddenLenses.push('V4 not reporting');
    // ══ 2026-09-26 · MC FEEDS PREDICTED SCORE, SO IT BELONGS HERE TOO ══
    // Andy, on both CAR@CLE and KC@MIA: "neither model tile produces
    // 48.3 ... the range's upper bound comes from somewhere not shown."
    //
    // Traced on KC @ MIA: the bound is the Monte Carlo lens — away 23.7,
    // home 24.5, total 48.26 — which ScoreRange adds via mc_probabilities
    // but which the NFL branch of this grid never listed. NCAAF has
    // always shown it. So the two sections drew from different lens sets
    // and the reader could not reconcile them.
    //
    // MC is not a rounding artifact of another model either: it has KC by
    // -0.8 where v3 has KC by 8.5. Hiding a lens that disagrees that
    // sharply while still using it to set the displayed range is the
    // worst of both. Adding it makes the sections agree and surfaces a
    // real disagreement the card was hiding.
    const _mcNfl = safeJSON(ctx?.mc_probabilities) || {};
    const _mcMargin = _mcNfl.mc_expected_margin;
    const _mcTotal = _mcNfl.mc_expected_total ?? _mcNfl.mc_mean_total;
    return [
      {name: 'v3', m: ctx?.projected_spread, t: ctx?.projected_total},
      ...(_v4Live ? [{name: 'v4', m: _v4m, t: _v4t}] : []),
      ...(_mcMargin != null || _mcTotal != null
          ? [{name: 'MC', m: _mcMargin, t: _mcTotal}] : []),
      {name: 'Panel',
        m: (ctx?.panel_pred_home_pts != null && ctx?.panel_pred_away_pts != null)
             ? (Number(ctx.panel_pred_home_pts) - Number(ctx.panel_pred_away_pts))
             : null,
        t: ctx?.panel_pred_total},
      ...(lrTile ? [lrTile] : []),
      ...(goatTile ? [goatTile] : []),
      {name: 'Conf', m: ctx?.signal_confluence_net, t: null},
    ];
  })() : [
    // Fallback for NHL / NBA / NCAAB / UFC — keep MC slot since those
    // sports may still populate mc_probabilities via their own simulators.
    {name: 'v3', m: ctx?.projected_spread, t: ctx?.projected_total},
    // ══ 2026-09-29 · THE NUMBER THE PICK IS BUILT ON WAS INVISIBLE ══
    // Andy: "Jerry write ups are bad." Once the NHL read lookup was fixed the
    // reads became reachable, and MTL @ TOR read: "The model has Toronto at
    // 48.2% to win against a 56% implied price ... stripping away any edge on
    // the moneyline" — attached to a pick of TORONTO ML, conviction 56.
    //
    // A read arguing against its own pick, and citing a number found nowhere
    // on the card. Both trace to one cause: this game has THREE win
    // probabilities and the card showed neither of the two that matter.
    //
    //   projected_home_wp   0.559  Elo — WHAT THE PICK IS BUILT ON
    //                              ("56% vs 53% implied — +2.5pp edge")
    //                              fetched by index.tsx, rendered NOWHERE
    //   _lr_ml_shadow       0.482  LR shadow, suggested_side NONE. The LR
    //                              tile exists only in the NCAAF/NFL
    //                              branches, and the LR chip suppresses
    //                              0.45-0.55 as PASS — so on NHL it is
    //                              invisible, yet the writer quoted it as
    //                              "the model"
    //   mc_p_home           0.470  Monte Carlo — the ONLY one on screen
    //
    // So a subscriber saw MC "A 53%" (Toronto a dog), read "48.2%" (a figure
    // absent from the card), and a Toronto ML pick justified by 55.9% they
    // were never shown. The pick's own basis was the one number hidden.
    //
    // Elo is added as its own labelled lens. It does not paper over the
    // disagreement — it discloses it: Elo 56% H against MC 47% H is a real
    // split between two models, and that is the receipt. Same call the NFL
    // MC comment above makes ("Adding it makes the sections agree and
    // surfaces a real disagreement the card was hiding").
    //
    // Rendered as H/A % rather than a spread because a win probability is not
    // a margin — identical treatment to the LR and MC tiles, so the grid
    // stays readable. Margin proxy drives border colour only.
    ...(String(gamesSport) === 'NHL' && ctx?.projected_home_wp != null
        && isFinite(Number(ctx.projected_home_wp))
      ? [(() => {
          const _eloP = Number(ctx.projected_home_wp);
          return {
            name: 'Elo',
            m: (_eloP - 0.5) * 10,   // sign-only proxy for colour; not shown
            t: null,
            displayMargin: _eloP >= 0.5
              ? `H ${Math.round(_eloP * 100)}%`
              : `A ${Math.round((1 - _eloP) * 100)}%`,
          };
        })()]
      : []),
    {name: 'v4', m: ctx?.v4_spread ?? ctx?.model_pred_spread,
                  t: ctx?.v4_total  ?? ctx?.model_pred_total},
    // ══ 2026-09-29 · MC IS A SIMULATION OF v3, NOT A SECOND OPINION ══
    // Andy: "MC and V3 both have CAR by .9 is that a bug?" Not a bug, but a
    // real one to catch. nhl_projection.project_and_simulate does:
    //
    //   proj = project_goals(...)
    //   mc   = monte_carlo(proj['home_goals'], proj['away_goals'])
    //
    // so the Monte Carlo draws from v3's OWN lambdas. Its expected margin
    // converges to v3's margin by construction. Measured across all 65 NHL
    // games on the board: mean |v3 − MC| of 0.023 goals, max 0.070, and 62
    // of 65 inside 0.05. They cannot meaningfully disagree.
    //
    // That matters because this grid is labelled "each model's read" and the
    // reader counts tiles to judge how many independent opinions back a pick.
    // Two tiles reading CAR 0.9 look like corroboration; it is one model
    // shown twice. Same defect Andy caught on NCAAF in September, recorded
    // then as "sp_plus_pred_total == projected_total; not a lens".
    //
    // The tile is NOT dropped, because MC carries things a point estimate
    // structurally cannot: a win probability and an overtime rate. So it now
    // shows what only it knows — mirroring how the LR tile shows "H 72%"
    // rather than inventing a spread. Margin still drives the border colour
    // so the side stays consistent with the other tiles.
    //
    // Scoped to NHL: this redundancy was measured on NHL. MLB/NCAAF/NFL have
    // their own MC paths and their own branches above; changing those needs
    // the same measurement first.
    {name: 'MC', m: mc.mc_expected_margin, t: mc.mc_expected_total ?? mc.mc_mean_total,
     ...(String(gamesSport) === 'NHL' && mc.mc_p_home != null ? {
       displayMargin: mc.mc_p_home >= 0.5
         ? `H ${Math.round(mc.mc_p_home * 100)}%`
         : `A ${Math.round((1 - mc.mc_p_home) * 100)}%`,
       // Overtime is hockey-specific and load-bearing: it is the main way a
       // -1.5 puck line loses, and it is not derivable from a point estimate.
       displayTotal: mc.mc_ot_rate != null
         ? `OT ${Math.round(mc.mc_ot_rate * 100)}%` : null,
     } : {})},
    // ══ 2026-09-29 · THE LR CHIP HAD NO TILE TO POINT AT ══
    // Measured on tonight's NHL board: 4 of 5 games render a cohort chip
    // reading "Model 64% HOME" / "Model 66% HOME" from
    // primary_play._lr_ml_shadow, while this grid — labelled as each model's
    // read — contained no LR tile at all. The tile is built only inside the
    // NCAAF and NFL branches above, so on NHL, NBA, NCAAB and UFC the chip
    // cited a model the reader could not find.
    //
    // Same defect as the Jerry read quoting a hidden 48.2% earlier today, and
    // I fixed the read side without noticing the chip side had it too. A
    // citation with no visible source is worse than no citation: it asks the
    // reader to trust a number they cannot check.
    //
    // Not scoped to one sport — the isFinite guard already self-scopes it.
    // Measured coverage: NHL 11/40 upcoming games, NBA 8/40. Where there is
    // no LR output the tile is simply absent, which the empty-lens rule below
    // would enforce anyway.
    //
    // Rendered H/A % rather than a spread because LR is a WIN probability,
    // not a margin — identical treatment to the NFL/NCAAF tiles and to MC and
    // Elo above, so the grid stays one format.
    ...(() => {
      const _lrP = Number((ctx?.primary_play as any)?._lr_ml_shadow?.p_home_win);
      if (!isFinite(_lrP)) return [];
      return [{
        name: 'LR',
        m: (_lrP - 0.5) * 10,     // sign-only proxy for colour; not shown
        t: null,
        displayMargin: _lrP >= 0.5
          ? `H ${Math.round(_lrP * 100)}%`
          : `A ${Math.round((1 - _lrP) * 100)}%`,
      }];
    })(),
    {name: 'Conf', m: ctx?.signal_confluence_net, t: null},
  ];

  // ══ 2026-09-28 · THE EMPTY-LENS RULE APPLIED TO EVERY OTHER SPORT ══
  // Andy on the NHL card: "v4 not puopulated". The 2026-09-26 fix for exactly
  // this lives inside the NFL branch above, so it never ran for NHL — and the
  // fallback branch hardcodes a v4 tile reading ctx.v4_spread ??
  // ctx.model_pred_spread, NEITHER of which exists on nhl_game_context. There
  // is no NHL v4 model at all, so that tile could never show anything.
  //
  // Same reasoning as the NFL fix: a model with no output should be ABSENT,
  // not present-and-blank, because a blank tile reads as "this model has no
  // opinion" — a much stronger claim than "this model did not run". And the
  // reader counts tiles to judge how many independent opinions back the pick,
  // so the absence is disclosed rather than silent.
  //
  // Scoped to the sports that reach the fallback branch. MLB, NCAAF and NFL
  // already curate their own lens lists, and re-filtering them here could
  // remove a tile one of them deliberately shows.
  if (!['MLB', 'NCAAF', 'NFL'].includes(String(gamesSport))) {
    const _dead = rows.filter((r: any) => r.m == null && r.t == null);
    if (_dead.length) {
      for (const d of _dead) _hiddenLenses.push(`${String(d.name).toUpperCase()} not reporting`);
      rows = rows.filter((r: any) => !(r.m == null && r.t == null));
    }
  }

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
      {_hiddenLenses.length > 0 && (
        <Text style={{color: C.textDim, fontSize: 9, fontStyle: 'italic', marginBottom: 4}}>
          {_hiddenLenses.join(' · ')}
        </Text>
      )}
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
          // 2026-09-14: CONF-specific "split" label. When net=0 but the
          // breakdown had 2+ signals that cancelled, the raw margin
          // renders as muted "0.00" and reads as empty. Show "N · split"
          // instead so users understand signals fired but balanced out.
          // For every other tile, net=0 legitimately means "no lens
          // signal" and the — display is right.
          let confSplitLabel: string | null = null;
          if (r.name === 'Conf' && r.m === 0) {
            const cb = safeJSON(ctx?.signal_confluence_breakdown);
            const cbN = cb && typeof cb === 'object' ? Object.keys(cb).length : 0;
            if (cbN >= 2) confSplitLabel = `${cbN} · split`;
          }
          return (
            <TouchableOpacity
              key={i}
              activeOpacity={helpAvailable ? 0.7 : 1}
              onPress={() => helpAvailable && setOpenLens(isOpen ? null : nameUp)}
              style={[
                styles.lens,
                {borderTopColor: missing ? C.border : sideColor(mgnSide), opacity: (missing && !confSplitLabel) ? 0.5 : 1},
                isOpen && {backgroundColor: C.accent + '18'},
              ]}
            >
              <View style={{flexDirection:'row', alignItems:'center', gap:2}}>
                <Text style={[styles.lensName, isOpen && {color: C.accent}]}>{nameUp}</Text>
                {helpAvailable && (
                  <Text style={{color: (isOpen ? C.accent : C.textMuted) + 'CC', fontSize:8, fontWeight:'700'}}>ⓘ</Text>
                )}
              </View>

              <Text numberOfLines={2}
                    style={[styles.lensMargin,
                            (r as any).smallText && styles.lensMarginSmall,
                            {color: confSplitLabel ? C.textMuted : (missing ? C.textDim : sideColor(mgnSide))}]}>
                {/* 2026-09-17: displayMargin override lets LR (probability
                    tile → "H 72%") and GOAT (composite tile → "KC · STRONG")
                    render in the tile without breaking the numeric format
                    used by v3/v4/panel/sp+/mc/conf. Sign color still driven
                    by r.m so the border-top hue stays consistent. */}
                {/* ══ 2026-09-27 · NAME THE TEAM, DON'T IMPLY IT ══
                    Andy: "Model tiles need a sign convention. It's unclear
                    whether MC +5.66 favors home or away, and with BAL at
                    -3.5 that could read as backing Dallas."

                    It DID mean Dallas — on BAL @ DAL, MC had mc_p_home
                    0.651 and a +5.66 home margin while the pick was BAL.
                    Direction was encoded only in the border-top hue, which
                    asks the reader to know a colour convention AND to know
                    that positive means home. Printing the abbreviation
                    removes both assumptions, and matches how the GOAT tile
                    ("BAL · LEAN") already reads.

                    Two decimals on a point spread is precision nobody has:
                    5.66 and 5.7 are the same forecast. Dropping to one also
                    buys back the width the team code costs, which is the
                    clipping Andy flagged in the same pass. */}
                {confSplitLabel ? confSplitLabel
                  : (r as any).displayMargin ? (r as any).displayMargin
                  : missing ? '—'
                  /* Conf is a net signal balance, not a point margin, so it
                     keeps the bare signed number — "DAL 1.0" would read as
                     a one-point spread it never claimed. */
                  /* 2026-09-29 · was f(r.m, 2), printing "+4.00" and "0.00"
                     for a value that is an INTEGER COUNT of net signals —
                     measured on tonight's NHL board: 1, 4, -2, 0. Two decimals
                     on a tally claims a precision that does not exist, and it
                     was the one thing on the grid that looked like a spread
                     while explicitly not being one. Integer now. */
                  : r.name === 'Conf' ? (r.m > 0 ? `+${f(r.m, 0)}` : f(r.m, 0))
                  : `${abbrev3(r.m > 0 ? ctx?.home_team : ctx?.away_team)} ${f(Math.abs(r.m), 1)}`}
              </Text>
              <Text style={[styles.lensTotal, {
                color: totDir === 'O' ? C.accent : totDir === 'U' ? C.sharp : C.textMuted,
              }]}>
                {(r as any).displayTotal
                  ? (r as any).displayTotal
                  : r.t == null ? '—' : `${totDir ?? '='} ${f(r.t, 1)}`}
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

// ─── SIGNALS ROW (2026-09-13 · game card model transparency) ───────
// Andy 9/13: "Are we surfacing any of these in model section in game
// card we should be with info markers to explain each what it is."
// PLUS "we should be tracking what each says and keeping record."
// Renders compact chip row under Model Consensus showing which of our
// signals actually FIRED for this game + tap to explain via glossary +
// LIVE HIT RATE from v_signal_records (populated by
// signal_attribution_snapshot + signal_attribution_grade cron pair).
// Chip color = supports pick (green) / neutral (grey) / disagrees (amber).
// Silent-hide if no signals materially fire.
function SignalsRow({ctx, gamesSport, cohortTagRecords = {}}: any) {
  const [openTerm, setOpenTerm] = useState<string | null>(null);
  const [signalRecords, setSignalRecords] = useState<Record<string, any>>({});

  // 2026-09-13: fetch per-(sport, signal_key, kind) hit rate rollup
  // from v_signal_records so each chip's tooltip can show real track
  // record: "LR AGREES: 68% (n=87) last 30d". Gated at 5+ samples per
  // key/kind combo (view enforces). Falls back gracefully to glossary-
  // only text when a signal has no graded history yet (Week 1-2 state).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // 2026-09-26: was `supabase.from(...)` — a name that does not exist
        // in this module (every other call site here goes through sb()).
        // The ReferenceError was swallowed by this function's own catch, so
        // the feature failed silently from the day it shipped: signal chips
        // have only ever shown glossary text, never the "68% (n=87)" track
        // record the fetch exists to provide. Nothing logged, nothing blank
        // on screen — the fallback path looked like the Week 1-2 state.
        const client = sb();
        if (!client) return;
        const {data, error} = await client.from('v_signal_records')
          .select('signal_key,kind,wins_30d,losses_30d,hit_pct_30d,wins_lifetime,losses_lifetime,hit_pct_lifetime')
          .eq('sport', gamesSport);
        if (cancelled || error) return;
        const map: Record<string, any> = {};
        for (const row of (data || [])) {
          map[`${row.signal_key}|${row.kind}`] = row;
        }
        setSignalRecords(map);
      } catch (_) { /* silent — SignalsRow degrades to definition-only */ }
    })();
    return () => { cancelled = true; };
  }, [gamesSport]);
  const pp = ctx?.primary_play || {};
  const pickSide = (pp.side || '').toUpperCase();  // HOME / AWAY / OVER / UNDER
  const isML = (pp.type === 'ml' || pp.type === 'spread' || pp.type === 'rl');

  type ChipDef = {term: string; label: string; value: string;
                  kind: 'ok' | 'warn' | 'neutral'};
  const chips: ChipDef[] = [];

  // EPA gap
  const homeEpa = Number(ctx?.home_off_epa_pp);
  const awayEpa = Number(ctx?.away_off_epa_pp);
  if (isFinite(homeEpa) && isFinite(awayEpa)) {
    const gap = homeEpa - awayEpa;
    if (Math.abs(gap) >= 0.05) {
      const leader = gap > 0 ? 'HOME' : 'AWAY';
      const supports = !isML || (leader === pickSide);
      chips.push({
        term: 'EPA_GAP',
        label: `EPA gap ${gap > 0 ? '+' : ''}${gap.toFixed(2)}`,
        value: `${leader} leads`,
        kind: supports ? 'ok' : 'warn',
      });
    }
  }

  // Cohort tags — chip color + value DRIVEN by real cohort_tag_records
  // hit rate, not assumed to be a "follow" signal. Andy 9/13 audit found
  // nfl_home_fav lifetime 48.5% (486-516 over 1002) — coin flip. Marking
  // it green was misleading. Rule now:
  //   hit >= 58% + n >= 30 → 'ok' (follow signal, real edge)
  //   hit 52-58% or n < 30 → 'neutral' (marginal, informational)
  //   hit < 52% + n >= 30 → 'warn' (coin flip or fade candidate)
  const cohortTags = Array.isArray(ctx?.cohort_tags) ? ctx.cohort_tags : [];
  const knownCohorts = ['heavy_home_dog', 'heavy_home_fav', 'div_home_underdog',
                         'div_home_cover', 'primetime_road_fav', 'home_fav',
                         'div_game', 'revenge', 'short_week', 'nfl_division_game',
                         'shootout'];
  for (const tag of cohortTags.slice(0, 3)) {
    const t = String(tag).toLowerCase();
    if (!knownCohorts.some(k => t.includes(k))) continue;
    const term = t.includes('heavy_home_fav') ? 'COHORT_HEAVY_HOME_FAV'
               : t.includes('heavy_home_dog') ? 'HEAVY_HOME_DOG'
               : t.includes('div_home_underdog') ? 'DIV_HOME_UNDERDOG'
               : t.includes('div_home_cover') ? 'COHORT_DIV_HOME_COVER'
               : t.includes('primetime_road_fav') ? 'PRIMETIME_ROAD_FAV'
               : t.includes('shootout') ? 'COHORT_SHOOTOUT'
               : t.includes('home_fav') ? 'COHORT_HOME_FAV'
               : t.includes('div') ? 'DIV_GAME'
               : t.includes('revenge') ? 'REVENGE'
               : t.includes('short_week') ? 'SHORT_WEEK'
               : 'CONF';
    // Real hit-rate lookup — cohort_tag_records is keyed by tag+market
    const rec = cohortTagRecords[`${t}|ats`] || cohortTagRecords[`${t}|ml`];
    let kind: 'ok' | 'warn' | 'neutral' = 'neutral';
    let value = 'active';
    if (rec && rec.sample_n >= 30) {
      const hp = Number(rec.hit_rate);
      // 2026-09-17: fade threshold tightened from < 52 → < 47. Andy
      // caught "Home Favorite · 48.51% · fade" — fading a 48.51%
      // trend nets 51.49% return, still BELOW breakeven ~52.4% at
      // standard -110 juice. Real fade signal starts when the trend
      // fires below 47% (fade rate 53%+ clears the juice). Widening
      // the neutral band 47-58 means we stop calling coin-flip
      // patterns "fade" when they aren't yet actionable.
      // 2026-09-26 · SAY WHOSE NUMBER THIS IS.
      // Andy: "Heavy Home Underdog · 51.65% for the fourth game
      // (UNLV@Akron, BSU@WMU, and here). Confirmed cohort base rate
      // rendered as game-specific."
      //
      // Right — it is the cohort's lifetime hit rate, identical on every
      // card the cohort fires on, and a bare "51.65%" next to this
      // game's teams reads as a probability for THIS game. Seeing the
      // same figure on four different matchups then reads as a bug.
      //
      // The n was already being shown on the thin branch below and
      // dropped on the confident one, which is backwards and breaks the
      // standing rule that every published percentage carries its
      // sample. "hits 51.7% (n=310)" is unambiguously a track record.
      const _n = rec.sample_n;
      if (hp >= 58) { kind = 'ok'; value = `hits ${hp}% (n=${_n}) · follow`; }
      else if (hp < 47) { kind = 'warn'; value = `hits ${hp}% (n=${_n}) · fade`; }
      else { kind = 'neutral'; value = `hits ${hp}% (n=${_n})`; }
    } else if (rec) {
      value = `hits ${Number(rec.hit_rate)}% (n=${rec.sample_n})`;
    }
    // 2026-09-15: use COHORT_LABEL_MAP for user-facing labels — was
    // showing raw "nfl home fav" text. Andy audit callout: "the whole
    // shtick is doing the math and surfacing to user in plain language".
    const prettyLabel = COHORT_LABEL_MAP[t] || tag.replace(/_/g, ' ');
    chips.push({term, label: prettyLabel, value, kind});
  }

  // LR shadow — pulled from primary_play._lr_ml_shadow / _lr_total_shadow
  const lrMl = pp._lr_ml_shadow || {};
  const lrTot = pp._lr_total_shadow || {};
  const lrMlP = Number(lrMl.p_home_win);
  if (isFinite(lrMlP)) {
    const lrSide = lrMlP >= 0.55 ? 'HOME' : lrMlP < 0.45 ? 'AWAY' : 'PASS';
    if (lrSide !== 'PASS') {
      // 2026-09-17: category-error fix. Andy caught "Model 78% HOME ·
      // disagrees" on NO +8.5 pick where BAL was -8.5 favorite. LR is
      // a WIN probability (who wins outright), not a COVER probability
      // (who covers the spread). BAL winning 78% ML is fully compatible
      // with NO covering +8.5 — in fact V3/V4/PANEL/CONF all landed
      // under 8.5 on that card, meaning consensus AGREED with the +8.5
      // pick even as LR "disagreed" on straight-up winner. Was flagging
      // agreement as disagreement on every spread pick where the model
      // model liked the underdog.
      //
      // Fix: only apply agree/disagree logic when the pick is on the
      // moneyline market (pp.type === 'ml') — that's the only case
      // where LR's win prob is the same question. For spread (rl) and
      // total picks, LR is a supplemental "who wins outright" lens —
      // show as neutral info, not agree/disagree.
      const isMoneyLine = pp.type === 'ml';
      const agrees    = isMoneyLine && lrSide === pickSide;
      const disagrees = isMoneyLine && lrSide !== pickSide;
      const pctPickSide = lrSide === 'HOME' ? lrMlP : (1 - lrMlP);
      chips.push({
        term: 'LR_SHADOW',
        label: `Model ${Math.round(pctPickSide * 100)}% ${lrSide}`,
        value: agrees ? 'agrees' : disagrees ? 'disagrees' : 'ml lens',
        kind: agrees ? 'ok' : disagrees ? 'warn' : 'neutral',
      });
    }
  }

  // LR TOTAL shadow chip — same signal exposure for totals. NCAAF has
  // v1.05 rolling-features LR total; NFL has none today (project memory
  // project_lr_totals_investigation_908). Show only when populated.
  const lrTotP = Number(lrTot?.p_over);
  if (isFinite(lrTotP)) {
    const lrTotSide = lrTotP >= 0.55 ? 'OVER' : lrTotP <= 0.45 ? 'UNDER' : 'PASS';
    if (lrTotSide !== 'PASS') {
      const pctPickSide = lrTotSide === 'OVER' ? lrTotP : (1 - lrTotP);
      const totAgree = (pickSide === lrTotSide);
      const totDisagree = (pickSide === 'OVER' || pickSide === 'UNDER') && pickSide !== lrTotSide;
      chips.push({
        term: 'LR_TOTAL',
        label: `Model ${Math.round(pctPickSide * 100)}% ${lrTotSide}`,
        value: totAgree ? 'agrees' : totDisagree ? 'disagrees' : 'total lean',
        kind: totAgree ? 'ok' : totDisagree ? 'warn' : 'neutral',
      });
    }
  }

  // Anchor status
  const anchorW = Number(ctx?.spread_anchor_weight);
  if (isFinite(anchorW) && anchorW > 0) {
    chips.push({
      term: 'ANCHOR',
      label: `Anchor ${anchorW.toFixed(2)}`,
      value: 'pulled toward market',
      kind: 'neutral',
    });
  }

  // GOAT model — from align_status.chips_extra
  const alignStatus = ctx?.align_status || {};
  const chipsExtra = Array.isArray(alignStatus.chips_extra) ? alignStatus.chips_extra : [];
  const goatChip = chipsExtra.find((c: any) => c?.key === 'goat');
  if (goatChip && goatChip.value && goatChip.value !== 'PASS') {
    const goatSide = String(goatChip.value).split('·')[0].trim().toUpperCase();
    const agrees = isML && (goatSide === pickSide || goatChip.value?.includes(pickSide));
    chips.push({
      term: 'GOAT',
      label: `GOAT ${goatChip.value}`,
      value: agrees ? 'agrees' : 'independent',
      kind: agrees ? 'ok' : 'neutral',
    });
  }

  if (chips.length === 0) return null;

  return (
    <View style={{marginBottom: 6}}>
      <View style={{flexDirection: 'row', flexWrap: 'wrap', gap: 6}}>
        {chips.map((c, i) => {
          // 2026-09-15: FIX undefined THEME reference — this file uses
          // local C palette (defined at line 103). THEME was a copy-paste
          // leak that crashed NFL game detail on open.
          const bg = c.kind === 'ok' ? C.accent + '18'
                   : c.kind === 'warn' ? C.warn + '18'
                   : C.surface;
          const fg = c.kind === 'ok' ? C.accent
                   : c.kind === 'warn' ? C.warn
                   : C.text;
          return (
            <TouchableOpacity
              key={`${c.term}-${i}`}
              onPress={() => setOpenTerm(openTerm === c.term ? null : c.term)}
              style={{
                flexDirection: 'row', alignItems: 'center', gap: 4,
                paddingHorizontal: 8, paddingVertical: 5,
                borderRadius: 6, backgroundColor: bg,
                borderWidth: 1, borderColor: C.border,
              }}>
              <Text style={{color: fg, fontSize: 10, fontWeight: '800',
                            letterSpacing: 0.3}}>{c.label}</Text>
              <Text style={{color: fg + 'AA', fontSize: 9, fontWeight: '600'}}>
                · {c.value}
              </Text>
              <Text style={{color: fg + 'CC', fontSize: 10, marginLeft: 2}}>ⓘ</Text>
            </TouchableOpacity>
          );
        })}
      </View>
      {openTerm && (() => {
        // 2026-09-16: _explainGlossary returns string|null (see glossary.ts
        // line 193 — `export function explain(term): string | null`).
        // Prior render did {help.help} which reads .help off a STRING and
        // renders undefined → user saw an empty expanded box with no text
        // (Andy expo audit "info not populating any text in box, expands
        // just no lettering"). Straight {help} renders the sentence.
        const help = _explainGlossary(openTerm);
        if (!help) return null;
        // Find the chip we tapped so we know its kind for the hit-rate lookup
        const chipObj = chips.find(c => c.term === openTerm);
        const kind = chipObj?.kind;
        // 2026-09-16 chip↔tooltip color match. Prior tooltip always painted
        // border + header text in accent green regardless of chip kind, so
        // tapping a yellow "warn" chip surfaced a green-bordered tooltip
        // titled "COHORT HOME FAV — WARN". Andy audit: "green background /
        // warn text mismatch". Now the accent color echoes the chip's kind.
        const kindColor = kind === 'warn' ? C.warn
                        : kind === 'ok'   ? C.accent
                        : C.textMuted;
        const rec = kind ? signalRecords[`${openTerm}|${kind}`] : null;
        const has30d = rec && (rec.wins_30d + rec.losses_30d) >= 5;
        const hasAll = rec && (rec.wins_lifetime + rec.losses_lifetime) >= 5;
        return (
          <View style={{marginTop: 8, padding: 10, backgroundColor: C.surface,
                        borderRadius: 8, borderLeftWidth: 3, borderLeftColor: kindColor}}>
            <Text style={{color: kindColor, fontSize: 10, fontWeight: '800',
                          letterSpacing: 0.5, marginBottom: 4}}>
              {openTerm.replace(/_/g, ' ')}
              {kind ? ` — ${kind.toUpperCase()}` : ''}
            </Text>
            {(has30d || hasAll) && (
              <View style={{flexDirection: 'row', gap: 12, marginBottom: 6}}>
                {has30d && (
                  <Text style={{color: C.text, fontSize: 11, fontWeight: '800',
                                fontVariant: ['tabular-nums']}}>
                    30d: <Text style={{color: C.accent}}>{rec.hit_pct_30d}%</Text>
                    {' '}({rec.wins_30d}-{rec.losses_30d})
                  </Text>
                )}
                {hasAll && (
                  <Text style={{color: C.textMuted, fontSize: 11, fontWeight: '700',
                                fontVariant: ['tabular-nums']}}>
                    Lifetime: {rec.hit_pct_lifetime}%
                    {' '}({rec.wins_lifetime}-{rec.losses_lifetime})
                  </Text>
                )}
              </View>
            )}
            <Text style={{color: C.text, fontSize: 12, lineHeight: 17}}>
              {help}
            </Text>
          </View>
        );
      })()}
    </View>
  );
}


// ─── HANDICAPPERS ROW ───────────────────────────────────────────────────
function HandicappersRow({picks, homeTeam, awayTeam, sport, records = {}}: any) {
  const _nonOCraw = (picks || []).filter((p: any) => p.source !== 'oddscrowd');
  // 2026-09-01: hard defense — parent Section is gated but if HandicappersRow
  // is ever mounted with no non-OC picks, render nothing rather than the
  // empty-state text that read as "we forgot to build this."
  if (_nonOCraw.length === 0) return null;

  // ══ 2026-09-27 · ONE SOURCE, ONE CURRENT PICK PER MARKET ══
  //
  // Andy: "'The Book' is listed on both BAL and DAL for the moneyline."
  //
  // external_picks stores one row PER PULL, not one per source-pick, and
  // nothing here collapsed them. On BAL @ DAL that game carried:
  //     ml  action         AWAY   pulled 2026-09-27   <- current
  //     ml  action         HOME   pulled 2026-09-25   <- stale
  //     ml  scoresandodds  HOME   x5 separate pulls
  //     rl  pickswise      HOME -4.0, then HOME 3.5, then HOME 3.5
  // So a handicapper who CHANGED their pick appeared on both sides at
  // once, and every source with repeat pulls was counted once per pull —
  // which is what inflates the "2 / 3 / 1" tallies beside each row.
  //
  // Keeping the newest pull per (source, surface) is the honest reading:
  // a handicapper has one live opinion per market, and it is their latest
  // one. Sorting descending and taking first-seen also makes the choice
  // deterministic rather than dependent on the order rows came back in.
  const _ts = (p: any) => {
    const t = Date.parse(p?.pulled_at || '');
    return isFinite(t) ? t : -Infinity;
  };
  const _byLatest = new Map<string, any>();
  for (const p of [..._nonOCraw].sort((a, b) => _ts(b) - _ts(a))) {
    const k = `${p.source}|${p.surface}`;
    if (!_byLatest.has(k)) _byLatest.set(k, p);
  }
  const nonOC = [..._byLatest.values()];

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
    // 2026-09-26: "The Dog and The Volume render with no record while The
    // Ticket 62-78 on the same line has one." The records exist — NCAAF
    // pickdawgz is 0-2 and covers 2-2 — and are correctly withheld by the
    // n>=5 gate, because a 0-2 is not a track record. But withholding it
    // SILENTLY makes a thin sample look like missing data sitting beside a
    // populated one. Say "n=2" so the absence is a fact rather than a gap.
    const thinRec = !hasRec && (w + l) > 0;
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
        {hasRec ? (
          <Text style={[
            styles.handiChipRecord,
            showBoost && {color: C.accent},
            showFade && {color: C.fade},
          ]}>
            {w}-{l}
          </Text>
        ) : thinRec ? (
          <Text style={[styles.handiChipRecord, {color: C.textDim}]}>
            n={w + l}
          </Text>
        ) : null}
      </View>
    );
  };

  // 2026-09-27 · Andy: "the spread handicapper count '3' wraps onto its own
  // line." It did: handiRow was flexWrap:'wrap' with the count on
  // marginLeft:'auto', so once the chips filled the line the count became
  // the next flex item, wrapped, and auto-margin shoved it to the right of
  // an otherwise empty row.
  //
  // The count is not part of the chip list and should not wrap with it, so
  // the chips now wrap inside their own flex:1 container and the count sits
  // outside it — pinned to the first line whatever the chips do.
  const bucketRow = (label: string, items: any[]) => (
    <View style={styles.handiRow}>
      <Text style={styles.handiSideLabel}>{label}</Text>
      <View style={styles.handiChipWrap}>
        {items.length === 0
          ? <Text style={styles.handiEmpty}>— none —</Text>
          : items.map(chip)}
      </View>
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
// Column widths for the Recent Schedule table, shared by the header and
// RecentGameRow so the two can never disagree. H2H trades width from the
// team column (3 chars + a venue letter) to ATS, which carries a team
// prefix there ("DEN -3") and overflows at the team-tab width on a phone.
const RS_FLEX     = {date: 0.9, opp: 1.6, score: 1.4, ats: 1.0, ou: 1.0};
const RS_FLEX_H2H = {date: 0.9, opp: 1.1, score: 1.3, ats: 1.6, ou: 1.0};

// Three sub-tabs: away / H2H / home. Cross-sport by design — same
// component renders MLB, NCAAF, and (future) NFL/NBA/NCAAB/NHL by
// filtering on sport. No client-side computation of records; matview
// is the single source of truth.
//
// Renders nothing when either team has zero rows (pre-season or matview
// not yet refreshed). Silent empty state — better than a placeholder.
function RecentScheduleCard({sport, homeTeam, awayTeam, season}: any) {
  const [awayRows, setAwayRows] = React.useState<any[]>([]);
  const [homeRows, setHomeRows] = React.useState<any[]>([]);
  const [h2hRows,  setH2hRows]  = React.useState<any[]>([]);
  const [tab, setTab] = React.useState<'away'|'h2h'|'home'>('away');
  const [loading, setLoading] = React.useState(true);

  // 2026-09-16 season-filter for football. Prior version pulled last 5
  // games regardless of season — on NFL Wk 1-4 and NCAAF Wk 1-5 that
  // filled the L5 tab with 4 prior-season games mixed with 1-2 current.
  // Users read "3-6 ATS" and "7-3 O/U" as a THIRD source of record data
  // separate from Situational Records + Team Stats (both properly
  // current-season-only after the blend-kill migration). Same principle
  // here: football → current season only, take what's played. MLB
  // unchanged (baseball rolls forward continuously, L5 always current).
  const seasonFilter = (sport === 'NFL' || sport === 'NCAAF')
    ? (Number(season) || (new Date().getMonth() >= 6 ? new Date().getFullYear() : new Date().getFullYear() - 1))
    : null;

  React.useEffect(() => {
    const client = sb();
    if (!client || !sport || !homeTeam || !awayTeam) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      const away = client.from('team_recent_games')
        .select('*').eq('sport', sport).eq('team', awayTeam);
      const home = client.from('team_recent_games')
        .select('*').eq('sport', sport).eq('team', homeTeam);
      const h2h  = client.from('team_recent_games')
        .select('*').eq('sport', sport).eq('team', homeTeam).eq('opp', awayTeam);
      if (seasonFilter != null) {
        away.eq('season', seasonFilter);
        home.eq('season', seasonFilter);
        // H2H stays cross-season — divisional matchups repeat only twice
        // per year, so an L5 in-season filter would empty the tab for
        // most non-divisional pairings. Historical H2H is genuine signal.
      }
      const [awayR, homeR, h2hR] = await Promise.all([
        away.order('seq', {ascending: true}).limit(5),
        home.order('seq', {ascending: true}).limit(5),
        h2h.order('game_date', {ascending: false}).limit(5),
      ]);
      if (cancelled) return;
      setAwayRows(Array.isArray(awayR?.data) ? awayR.data : []);
      setHomeRows(Array.isArray(homeR?.data) ? homeR.data : []);
      setH2hRows(Array.isArray(h2hR?.data) ? h2hR.data : []);
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [sport, homeTeam, awayTeam, seasonFilter]);

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

      {/* Column header. 2026-09-25: on H2H every row has the SAME opponent,
          so an "OPP" column is dead weight there — and worse, users read
          that abbrev as the winner. Andy: "it will have final score 34-30
          and a W, but you dont really know who W." On H2H the column names
          the winner instead, and the ATS chip gains a team prefix, so the
          two columns trade width. Header and rows read the SAME flex map
          (RS_FLEX / RS_FLEX_H2H) so they cannot drift out of alignment. */}
      <View style={rsStyles.headRow}>
        {(() => {
          const f = tab === 'h2h' ? RS_FLEX_H2H : RS_FLEX;
          return (
            <>
              <Text style={[rsStyles.hCol, {flex: f.date}]}>DATE</Text>
              <Text style={[rsStyles.hCol, {flex: f.opp, textAlign: 'left'}]}>
                {tab === 'h2h' ? 'WON' : 'OPP'}
              </Text>
              <Text style={[rsStyles.hCol, {flex: f.score}]}>SCORE</Text>
              <Text style={[rsStyles.hCol, {flex: f.ats}]}>ATS</Text>
              <Text style={[rsStyles.hCol, {flex: f.ou}]}>O/U</Text>
            </>
          );
        })()}
      </View>

      {/* Rows */}
      {rowsSorted.length === 0 ? (
        <Text style={rsStyles.empty}>
          {tab === 'h2h' ? 'No prior head-to-head' : 'No games logged yet this season'}
        </Text>
      ) : (
        <>
          {rowsSorted.map((r, i) => (
            <RecentGameRow key={r.game_id || i} row={r} h2h={tab === 'h2h'} />
          ))}
          {/* 2026-09-17: thin-data note. Andy caught NO@BAL Week 2
              rendering with 1 row and reading as broken. Below-3 hints
              at the season stage so users know it's not an error — just
              early data. Applies to team tabs only; H2H can legitimately
              be 1-3 rows even mid-season. */}
          {tab !== 'h2h' && rowsSorted.length < 3 && (
            <Text style={rsStyles.empty}>
              {rowsSorted.length === 1
                ? 'Early season — 1 game logged so far'
                : `Early season — ${rowsSorted.length} games logged so far`}
            </Text>
          )}
        </>
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

// `h2h` switches the row from "one team's schedule" framing to "this
// matchup's history" framing. 2026-09-25 — the H2H tab was genuinely
// unreadable before, and for a structural reason worth spelling out.
//
// Every column here is written from the perspective of row.team, which on
// the H2H query is ALWAYS the current game's home team. On the away/home
// tabs the active tab names that subject, so "W 34-30" and an ATS chip of
// "-3" are unambiguous. On H2H nothing named the subject, so:
//   · "W 34-30" told you someone won 34-30, not who.
//   · the OPP abbrev is the *opponent*, which reads as the winner.
//   · "-3" gave a spread with no indication whose it was.
// Three cells, none of them attributable. Fixed by naming the subject in
// the cells themselves rather than adding a legend above the table.
function RecentGameRow({row, h2h = false}: any) {
  const isHome = !!row.is_home;
  const isNeutral = !!row.is_neutral;
  const opp = row.opp || '—';
  const subject = row.team || '—';
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
  // ══ 2026-09-28 · A PRESEASON GAME HAS NO LINE TO COVER ══
  // Andy on the NHL card: "recent scheudle is semi populated some games
  // missing o and u and ats column". Measured: team_recent_games has
  // spread_line and total_line on 147 of 300 NHL rows, and the blanks are
  // all EXHIBITIONS — books do not post sides or totals on most preseason
  // hockey, so there is genuinely no ATS or O/U result to show.
  //
  // A bare em-dash reads as missing data. "PRE" says the game was an
  // exhibition, which is information: it is also why that row should not be
  // read as form. NHL game IDs encode the type at digits 4-6 (01 preseason),
  // the same test used to keep exhibitions out of strength-of-record and out
  // of the situational matview.
  const _isPreseason = (() => {
    const gid = String(row.game_id ?? '');
    return gid.length >= 6 && gid.slice(4, 6) === '01';
  })();
  const _emptyCell = _isPreseason ? 'PRE' : '—';

  // Compact date "MM/DD" — with the year on H2H, where rows span seasons.
  // 2026-09-26: Andy read the H2H list as "dates out of chronological
  // order ... 9/10, 12/8, 12/20, 11/27, 10/7". The query orders
  // game_date DESC and is correct; what was missing is that those five
  // rows are 2022, 2018, 2014, 2010 and 2006. Hiding the year made a
  // correctly ordered multi-season list look shuffled. Third report of
  // this, so the year goes on the H2H tab.
  // ══ 2026-09-27 · A DATE-ONLY STRING IS NOT AN INSTANT ══
  //
  // Andy: "Game dates show 9/12 and 9/19, which are Saturdays. That looks
  // like the classic bug where a date-only value is parsed as UTC and
  // shifts back a day in ET." Exactly right — nfl_game_context holds those
  // games on 2026-09-13 and 2026-09-20, both Sundays.
  //
  // `new Date('2026-09-13')` is specified to parse a date-ONLY string as
  // UTC midnight, but getMonth()/getDate() then read it in the device's
  // LOCAL zone. Anywhere west of Greenwich that lands on the previous
  // evening, so every NFL Sunday rendered as Saturday.
  //
  // game_date is a calendar date, not a moment, so the fix is to never
  // build a Date from it — read the Y-M-D fields straight off the string.
  // Adding a timezone offset would only move the bug to a different set of
  // users; there is no offset that makes a calendar date into an instant.
  let dateShort = '';
  try {
    const m = String(row.game_date || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) {
      const [, yy, mm, dd] = m;
      dateShort = h2h
        ? `${Number(mm)}/${Number(dd)}/${yy.slice(2)}`
        : `${Number(mm)}/${Number(dd)}`;
    }
  } catch {}

  // 2026-09-26 · THE LINE ON THE CHIP MUST BELONG TO THE TEAM NAMED.
  //
  // Andy: "H2H cover coloring is inverted on two of five rows ... 12/20
  // CAR won 17-13, CLE -6, green — CLE didn't cover."
  //
  // Measured across both sports, spread_result is 100% self-consistent
  // but the two sports store spread_line from OPPOSITE perspectives:
  //     NFL    (n=14,226)  result matches  margin - line
  //     NCAAF  (n=12,376)  result matches  margin + line
  // Same sign-convention split as close_spread.
  //
  // So the COLOUR was right all along and the LABEL was wrong: on that
  // row Cleveland was +6 and did cover a 4-point loss, but the chip
  // printed the opponent's -6 next to Cleveland's name.
  //
  // Rather than keep a per-sport sign table that the next sport breaks,
  // derive the owner from the row itself: spread_result is ground truth,
  // so the subject's line is whichever sign makes the arithmetic agree
  // with the recorded result. Self-correcting per row, and it degrades
  // to the stored value when the row is ungraded.
  const _subjectLine = (() => {
    const L = spreadLine == null ? null : Number(spreadLine);
    if (L == null || !isFinite(L)) return null;
    if (scoreUs == null || scoreThem == null) return L;
    if (spreadRes !== 'won' && spreadRes !== 'lost') return L;
    const margin = Number(scoreUs) - Number(scoreThem);
    const won = spreadRes === 'won';
    if (((margin + L) > 0) === won) return L;      // stored as subject's
    if (((margin - L) > 0) === won) return -L;     // stored as opponent's
    return L;
  })();

  const venuePrefix = isNeutral ? 'vs' : isHome ? 'vs' : '@';

  // H2H: who actually won, and show the score winner-first so the two
  // numbers read in the same order as the name beside them. `won` is
  // relative to the subject, so the winner is subject when true, opp when
  // false, and unknown (tie / ungraded) when null.
  const winner = wonSU === true ? subject : wonSU === false ? opp : null;
  const hiLo = (scoreUs != null && scoreThem != null)
    ? [Math.max(Number(scoreUs), Number(scoreThem)), Math.min(Number(scoreUs), Number(scoreThem))]
    : null;
  const scoreText = (scoreUs != null && scoreThem != null)
    ? (h2h && winner && hiLo ? `${hiLo[0]}-${hiLo[1]}` : `${scoreUs}-${scoreThem}`)
    : '—';

  const F = h2h ? RS_FLEX_H2H : RS_FLEX;

  return (
    <View style={rsStyles.dataRow}>
      <Text style={[rsStyles.cell, {flex: F.date, color: C.textMuted}]}>{dateShort}</Text>
      {h2h ? (
        // WON column. The venue marker moves onto the SUBJECT (the current
        // home team) so "@" still reads correctly — it says where the game
        // was played relative to tonight's home side, which is the part of
        // H2H that carries signal.
        <Text style={[rsStyles.cell, {flex: F.opp, textAlign: 'left'}]} numberOfLines={1}>
          <Text style={{color: C.textMuted, fontSize: 9}}>
            {isNeutral ? 'N ' : isHome ? 'H ' : 'A '}
          </Text>
          <Text style={{
            color: winner ? C.text : C.textMuted, fontWeight: '700',
          }}>
            {winner ? abbrev3(winner) : 'TIE'}
          </Text>
        </Text>
      ) : (
        <Text style={[rsStyles.cell, {flex: F.opp, textAlign: 'left'}]} numberOfLines={1}>
          <Text style={{color: C.textMuted}}>{venuePrefix} </Text>
          <Text style={{color: C.text, fontWeight: '700'}}>{abbrev3(opp)}</Text>
          {isNeutral ? <Text style={{color: C.textMuted, fontSize: 9}}>  N</Text> : null}
        </Text>
      )}
      {/* Score chip w/ W/L color. 2026-09-01: flattened nested Text —
          prior nested <Text style={{fontWeight:'700'}}> had no explicit
          color, and RN's inheritance through conditional-falsy style
          arrays isn't reliable → score rendered black on dark background
          for games w/ null won. Combining into one Text ensures the
          semantic color (win/loss/muted) applies to the whole string. */}
      <View style={{flex: F.score, alignItems: 'center'}}>
        <View style={[
          rsStyles.chip,
          wonSU === true  && rsStyles.chipWin,
          wonSU === false && rsStyles.chipLoss,
        ]}>
          {/* 2026-09-01 v4: text stays bright cream ALWAYS. See RecordPill
              rationale — colored text on tinted bg = muddy contrast that
              reads as "black text." Background alone signals win/loss. */}
          {/* On H2H the WON column already names the winner and the score
              is printed winner-first, so a W/L letter would be describing
              the subject while the numbers describe the winner — the exact
              ambiguity this tab had. Letter is team-tab only. */}
          <Text style={rsStyles.chipText}>
            {(h2h ? '' : wonSU === true ? 'W ' : wonSU === false ? 'L ' : '') + scoreText}
          </Text>
        </View>
      </View>
      {/* ATS chip */}
      <View style={{flex: F.ats, alignItems: 'center'}}>
        {spreadRes ? (
          <View style={[
            rsStyles.chip,
            spreadRes === 'won'  && rsStyles.chipWin,
            spreadRes === 'lost' && rsStyles.chipLoss,
            spreadRes === 'push' && rsStyles.chipPush,
          ]}>
            {/* spread_line is the SUBJECT's line, and spread_result is
                whether the subject covered it. On H2H that subject was
                never named, so "+3 (green)" left you guessing which side
                the number belonged to. Prefixing the abbrev makes the chip
                self-contained: "DEN -3" green = Denver covered -3. */}
            <Text style={rsStyles.chipText}>
              {h2h && subject !== '—' ? `${abbrev3(subject)} ` : ''}
              {_subjectLine != null ? (_subjectLine > 0 ? '+' : '') + _subjectLine : '—'}
            </Text>
          </View>
        ) : <Text style={rsStyles.dashCell}>{_emptyCell}</Text>}
      </View>
      {/* O/U chip */}
      <View style={{flex: F.ou, alignItems: 'center'}}>
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
        ) : <Text style={rsStyles.dashCell}>{_emptyCell}</Text>}
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
function SituationalCard({sport, homeTeam, awayTeam, season, homeML, awayML}: any) {
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
      // Sample-size gauge: overall spread record (proxy for total game count).
      // 2026-09-06: bug fix — prior code read `overall.games` which is a
      // non-existent column in team_situational_records. `Number(undefined)`
      // → NaN → `|| 0` → 0. Result: the current-season game count ALWAYS
      // registered as 0, which flipped the prior-season fallback ON for
      // every game unconditionally. Users saw "RECORDS" (last season)
      // badges on Reds/Phillies mid-current-season even though the DB
      // held real 2026 rows. Now: sum wins + losses + pushes from the
      // actual columns.
      const _gamesFor = (rows: any[]) => {
        const overall = rows.find(r => r.market === 'spread' && r.filter === 'overall');
        if (!overall) return 0;
        const w = Number(overall.wins) || 0;
        const l = Number(overall.losses) || 0;
        const p = Number(overall.pushes) || 0;
        return w + l + p;
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
  // ══ 2026-09-26 · FAV/DOG MUST FOLLOW THIS GAME'S ROLES ══
  // This row was hardcoded away->as_dog, home->as_fav. On UNLV @ Akron the
  // away team is a 13.5-point FAVOURITE, so the card showed UNLV under
  // "AS DOG" (empty, because they have not been a dog) and Akron under
  // "AS FAV" — each team's opposite role, and the two splits that actually
  // matter here were not shown at all.
  //
  // Favourite is derived from the MONEYLINE, not the spread. close_spread's
  // sign convention differs by sport and has already caused one grading bug
  // (project_close_spread_sign_bug_914); the cheaper ML is the favourite in
  // every sport, with no convention to get wrong. Falls back to the old
  // fixed layout when no prices are available.
  const _hml = Number(homeML), _aml = Number(awayML);
  const _havePrices = isFinite(_hml) && isFinite(_aml);
  const homeIsFav = _havePrices ? _hml < _aml : true;
  const awayRoleFilter = homeIsFav ? 'as_dog' : 'as_fav';
  const homeRoleFilter = homeIsFav ? 'as_fav' : 'as_dog';
  const awayRoleLabel  = homeIsFav ? 'As Dog' : 'As Fav';
  const homeRoleLabel  = homeIsFav ? 'As Fav' : 'As Dog';

  // Splits skipped because neither team has played one yet — named in a
  // footnote so the row count is never silently different between cards.
  const _droppedSplits: string[] = [];
  const rows: [string, string, string, string][] = [
    ['Overall',   'overall', 'Overall',   'overall'],
    ['Last 10',   'l10',     'Last 10',   'l10'],
    ['Away',      'road',    'Home',      'home'],
    [awayRoleLabel, awayRoleFilter, homeRoleLabel, homeRoleFilter],
  ];

  // Splits suppressed because their records repeat a split already shown
  // (see the note at the render loop). Named in a footnote for the same
  // reason dropped splits are: a silently shorter list reads as a fault.
  const _dupSplits: string[] = [];
  const _sitSeen = new Set<string>();
  const _sitSig = (rec: any): string =>
    rec == null ? 'none'
    : `${Number(rec.wins) || 0}-${Number(rec.losses) || 0}-${Number(rec.pushes) || 0}`;

  // Every split being below the sample floor is ONE fact about the week,
  // not one fault per row. SIT_MIN_N is the same floor SitRow applies.
  const _allThin = rows.every(([, fa, , fh]) => {
    const a = awayByFilter[fa], h = homeByFilter[fh];
    const aN = (Number(a?.wins) || 0) + (Number(a?.losses) || 0);
    const hN = (Number(h?.wins) || 0) + (Number(h?.losses) || 0);
    if (aN === 0 && hN === 0) return true;   // dropped rows don't argue either way
    return (aN > 0 && aN < SIT_MIN_N) || (hN > 0 && hN < SIT_MIN_N);
  });

  return (
    <View style={{gap: 10}}>
      {/* 2026-09-01: prior-season badge when sample too thin for
          current season (<5 games). Prevents "1-0" early-season noise
          from displacing prior season's real signal (5-7 etc).
          Remotely killable via feature_flags — set enabled=false to
          hide without app update. Auto-expires ~Week 5-6 of season
          when N crosses threshold naturally. */}
      {/* 2026-09-02 UX: was full warning banner that visually collided
          with NFLJerryLockNote (grey). Reduced to inline pill next to
          the records — same info, doesn't compete with the top banner. */}
      {usingPriorSeason && badgeEnabled && (
        <View style={{flexDirection:'row', alignItems:'center', gap: 6}}>
          <View style={{
            backgroundColor: C.warnDim, borderRadius: 4,
            paddingVertical: 2, paddingHorizontal: 6,
          }}>
            <Text style={{color: C.warn, fontSize: 9, fontWeight: '700', letterSpacing: 0.04}}>
              {usingPriorSeason} RECORDS
            </Text>
          </View>
          <Text style={{color: C.textDim, fontSize: 10}}>
            {seasonForQuery} sample too thin — prior season shown
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

      {/* Filter rows — 2026-09-16: hide when BOTH teams have no games
          for the (market × filter) cell. Andy screenshot: PSU@ORE showed
          "7-3 ATS road" for Portland State with nothing for Oregon —
          asymmetric records read as data bug to users. If neither side
          has data for the filter, skip the row entirely. */}
      {/* 2026-09-26: Andy — "the AWAY / HOME row is missing from
          situational records on this card. CAR@CLE had four rows; this
          has three." Correct, and dropping it is right: on KC @ MIA both
          of Kansas City's games were at home and both of Miami's were on
          the road, so neither team has a single game in that split.
          Rendering two empty boxes would be worse.

          But a silently shorter list makes the reader wonder what they
          missed — the same objection as the hidden V4 lens. Dropped
          splits are now named underneath. */}
      {/* ══ 2026-09-27 · A ROW THAT REPEATS ANOTHER ROW IS NOISE ══
          Andy, on BAL @ DAL: "'Last 10' duplicates 'Overall' at 1-1. Hide
          it until more than 2 games have been played."

          Right, and it generalises: in week 3 a team's LAST 10 *is* its
          OVERALL, and AS FAV can equal AWAY when every road game was as a
          favourite. Each repeat costs a row and pays nothing, and a screen
          of five identical 1-1 boxes is most of why this section reads as
          broken even when it is correct.

          Deduped on the records themselves rather than by hard-coding
          "hide LAST 10 before N games" — that would need a per-filter game
          count the client does not have, and would keep the row on a game
          where it happens to differ. Identical content is the actual test. */}
      {(() => { _sitSeen.clear(); return null; })()}
      {rows.map(([labelL, filterA, labelR, filterH], i) => {
        const lr = awayByFilter[filterA];
        const rr = homeByFilter[filterH];
        const lTot = (Number(lr?.wins) || 0) + (Number(lr?.losses) || 0) + (Number(lr?.pushes) || 0);
        const rTot = (Number(rr?.wins) || 0) + (Number(rr?.losses) || 0) + (Number(rr?.pushes) || 0);
        if (lTot === 0 && rTot === 0) { _droppedSplits.push(`${labelL}/${labelR}`); return null; }
        const sig = _sitSig(lr) + '|' + _sitSig(rr);
        if (_sitSeen.has(sig)) { _dupSplits.push(labelL === labelR ? labelL : `${labelL}/${labelR}`); return null; }
        _sitSeen.add(sig);
        return (
          <SitRow
            key={i}
            leftLabel={labelL}  leftRec={lr}
            rightLabel={labelR} rightRec={rr}
            market={market}
            showThinNote={!_allThin}
            // ══ 2026-09-28 · A PUCK LINE IS ALWAYS ±1.5, SO ATS IS STRUCTURAL ══
            // Andy: "Puck-line ATS records are structural. Favorites at -1.5
            // cover roughly 35-40% of the time by design, so 34-49 'as fav' in
            // red and 22-11 'as dog' in green aren't edges."
            //
            // Correct, and this row is where it bites hardest. Hockey has no
            // variable spread: the favourite always lays -1.5 and must win by
            // two, the dog always takes +1.5 and cashes on any one-goal loss.
            // So the fav/dog row puts Florida's DOG record (22-11, 66.7%)
            // beside Carolina's FAV record (34-49, 41.0%) and colours Florida
            // green — comparing two different structural regimes and calling
            // the difference an edge. Both numbers are near league-normal for
            // their role.
            //
            // The other rows survive because both teams are measured in the
            // same terms. Only this one crosses regimes, and only in hockey;
            // in football the two sides of a spread are symmetric.
            noCompare={String(sport) === 'NHL' && market === 'spread'
                       && (filterA === 'as_dog' || filterA === 'as_fav')}
            noCompareNote="puck line is always ±1.5 — favourite and underdog records aren't comparable"
          />
        );
      })}

      {/* Say "small sample" ONCE. The per-row note repeated under every
          line was five copies of the same sentence, which reads as five
          separate faults rather than one early-season fact. */}
      {_allThin && (
        <Text style={{color: C.textDim, fontSize: 9, fontStyle: 'italic',
                      textAlign: 'center', marginTop: 2}}>
          too few games played to call an edge in any split yet
        </Text>
      )}
      {_dupSplits.length > 0 && (
        <Text style={{color: C.textDim, fontSize: 9, fontStyle: 'italic',
                      textAlign: 'center', marginTop: 2}}>
          {`${_dupSplits.join(' · ')} — same as a split already shown`}
        </Text>
      )}

      {_droppedSplits.length > 0 && (
        <Text style={{color: C.textDim, fontSize: 9, fontStyle: 'italic',
                      textAlign: 'center', marginTop: 2}}>
          {`${_droppedSplits.join(' · ')} — neither team has played that split yet`}
        </Text>
      )}

      {loading && <Text style={rsStyles.empty}>Loading…</Text>}
    </View>
  );
}

// ══ 2026-09-25 · COLOUR BY ADVANTAGE, NOT BY ABSOLUTE QUALITY ══
// Andy: "college football situational record colouring red/green for
// advantage, just not uniform."
//
// The pill used to grade each team's record on its own — is 3-1 "good?" At
// NCAAF week 4 the answer is almost always "too few games to say", so
// football stayed grey while MLB (140 games) lit up. Two sports, two
// behaviours, from one rule that only ever suited baseball.
//
// Comparing the two teams instead is both more useful and more honest. The
// row already puts the same split side by side (Away ATS vs Home ATS), and
// "Pitt is 3-0 here, Akron is 1-2" is a true statement at n=3 in a way that
// "3-0 is a strong record" is not. The record itself stays visible in the
// pill, so the reader can always see the sample behind the colour. This is
// the same comparative model the Team Stats panel already uses, which is
// what makes it uniform across sports.
//
// TOTALS USE A DIFFERENT RULE, they are not excluded. 2026-09-26: the first
// version skipped them on the reasoning that a "win" there is the OVER, so
// one team being more over than the other is a tendency rather than an
// advantage. That reasoning was right about the semantics and wrong about
// the product — it left the Total tab entirely grey, including UNLV at
// O 0 · U 3 against Akron at O 2 · U 1, which is the most one-sided split
// on the card. Andy, correctly: "the most one-sided split on the card and
// it's gray."
//
// So totals colour by LEAN DIRECTION rather than by advantage: green marks
// a team whose games go OVER, red marks UNDER, and the section hint says so
// on that tab. Nothing is asserted about which is good. Spread and ML keep
// the head-to-head comparison, where better genuinely means better.
// ══ 2026-09-26 · RAW RATE, NOT LAPLACE ══
// The smoothed version collapsed genuinely different records onto the same
// number, because +1/+2 rewards a tiny sample far more than a larger one:
//
//     RMU 0-1  raw 0.000   Laplace (0+1)/(1+2) = 0.333
//     BUF 1-3  raw 0.250   Laplace (1+1)/(4+2) = 0.333   <- identical
//
// A real 25-point gap became a tie, and the row rendered grey. That is the
// moneyline complaint: ML records are plain W-L with the smallest, most
// lopsided samples, so they hit this collapse constantly while spread and
// total — which vary with the line — mostly escaped it.
//
// Compare what the user can actually see. The pill prints the record, so
// the sample is never hidden behind the colour, and a reader who sees
// "0-1 vs 1-3" and expects the 1-3 side to be greener is simply right.
function _sitRate(rec: any): number | null {
  const w = Number(rec?.wins) || 0;
  const l = Number(rec?.losses) || 0;
  if (w + l === 0) return null;
  return w / (w + l);
}

// Raised from 0.10 with the switch to raw rates — unsmoothed rates spread
// much wider, so the old floor would have coloured near-noise.
const SIT_EDGE_MIN = 0.15;   // head-to-head gap needed on spread / ML
const SIT_LEAN_MIN = 0.60;   // own-rate needed to call a total a lean

// ══ 2026-09-26 · THE LEGEND PROMISED A SAMPLE GATE THAT DID NOT EXIST ══
// Andy: "the thin-sample rule doesn't match what's implemented. AWAY 1-0
// vs an empty HOME renders neutral; AS FAV 1-0 vs AS DOG 1-1 renders full
// green/red. Both are <=3 games. The = marker appears to fire on exact
// ties, not on sample size."
//
// Exactly right. The only reason a 1-0 / 0-games row looked neutral is
// that one side had NO record at all and the comparison bailed — not
// because anything checked the sample. 1-0 against 1-1 is a 0.50 rate
// gap, clears SIT_EDGE_MIN of 0.15 easily, and painted full green/red off
// three games total. The legend has been claiming a gate the code never
// had, which is worse than having no legend.
//
// Two decided games per side is the floor for saying one team is better
// at something. Below it the row renders neutral and says why.
const SIT_MIN_N = 3;

function _sitN(rec: any): number {
  return (Number(rec?.wins) || 0) + (Number(rec?.losses) || 0);
}

function SitRow({leftLabel, leftRec, rightLabel, rightRec, market,
                 showThinNote = true, noCompare = false,
                 noCompareNote = ''}: any) {
  const lr = _sitRate(leftRec);
  const rr = _sitRate(rightRec);
  const lN = _sitN(leftRec), rN = _sitN(rightRec);
  // A side with no games at all is a different state from a thin one —
  // "no home games yet" is information, an empty dashed box is not.
  const noLeft = lN === 0, noRight = rN === 0;
  const thin = (lN > 0 && lN < SIT_MIN_N) || (rN > 0 && rN < SIT_MIN_N);
  let leftEdge: 'good' | 'bad' | 'even' | null = null;
  let rightEdge: 'good' | 'bad' | 'even' | null = null;
  if (market === 'total') {
    // Each side judged on its OWN lean — the two teams are not competing
    // for the same outcome here, they each just tend over or under.
    const lean = (v: number | null): 'good' | 'bad' | null =>
      v == null ? null
      : v >= SIT_LEAN_MIN ? 'good'
      : v <= 1 - SIT_LEAN_MIN ? 'bad'
      : null;
    leftEdge = lean(lr);
    rightEdge = lean(rr);
  } else if (lr != null && rr != null) {
    if (Math.abs(lr - rr) >= SIT_EDGE_MIN) {
      leftEdge  = lr > rr ? 'good' : 'bad';
      rightEdge = lr > rr ? 'bad' : 'good';
    } else {
      // ══ 2026-09-26 · A TIE IS NOT MISSING DATA ══
      // Andy, 7th report: "moneyline tab still not rendering red and green,
      // spread and total are fine." It was not a rendering bug. Moneyline
      // records are plain win-loss, so two teams at week 4 tie constantly —
      // on CSU @ UTSA both are 2-1 overall AND 2-1 in the L10, while spread
      // (2-1 vs 3-0) and total (3-0 vs 1-2) differ and colour normally.
      //
      // So the comparison was right and the OUTPUT was still wrong: a
      // compared-and-level row rendered identically to a row with no data,
      // and a tab that is mostly grey reads as broken no matter how correct
      // the logic is. "These two are level" is a real answer and now looks
      // like one.
      leftEdge = 'even';
      rightEdge = 'even';
    }
  }

  // Not-comparable gate. Runs before the sample gate because it is a
  // statement about the MARKET, not the sample: no number of games makes a
  // -1.5 favourite's cover rate comparable to a +1.5 underdog's.
  if (noCompare) {
    leftEdge = null;
    rightEdge = null;
  }

  // Sample gate LAST, so it overrides every colouring branch above. A
  // three-game sample cannot support "this team is better at covering",
  // and the legend has been promising this gate all along.
  if (thin) {
    leftEdge = null;
    rightEdge = null;
  }

  return (
    <View>
      <View style={sitStyles.row}>
        <View style={sitStyles.side}>
          <Text style={sitStyles.rowLabel}>{leftLabel}</Text>
          <RecordPill rec={leftRec} market={market} edge={leftEdge}
                      emptyNote={noLeft ? leftLabel : null} />
        </View>
        <View style={sitStyles.side}>
          <Text style={[sitStyles.rowLabel, {textAlign: 'right'}]}>{rightLabel}</Text>
          <RecordPill rec={rightRec} market={market} edge={rightEdge}
                      emptyNote={noRight ? rightLabel : null} />
        </View>
      </View>
      {thin && showThinNote && (
        <Text style={{color: C.textDim, fontSize: 9, fontStyle: 'italic',
                      textAlign: 'center', marginTop: -2, marginBottom: 4}}>
          too few games to call an edge
        </Text>
      )}
      {/* 2026-09-28: the verdict the "=" used to carry, in words. Only when
          BOTH sides are level — one 'even' pill without the other would mean
          the comparison never ran. Suppressed when the thin note is already
          showing, since "too few games" is the stronger statement and two
          captions under one row is noise. */}
      {noCompare && noCompareNote ? (
        <Text style={{color: C.textDim, fontSize: 9, fontStyle: 'italic',
                      textAlign: 'center', marginTop: -2, marginBottom: 4}}>
          {noCompareNote}
        </Text>
      ) : null}
      {!noCompare && !thin && leftEdge === 'even' && rightEdge === 'even' && (
        <Text style={{color: C.textDim, fontSize: 9, fontStyle: 'italic',
                      textAlign: 'center', marginTop: -2, marginBottom: 4}}>
          evenly matched — no edge either way
        </Text>
      )}
    </View>
  );
}

function RecordPill({rec, market, edge, emptyNote}: any) {
  // 2026-09-06 dashes-everywhere bug fix. Prior guard read `rec.games`
  // — a column that does NOT exist in team_situational_records. The
  // matview stores wins/losses/pushes only; games is derived at render
  // time. Result: EVERY MLB row returned `rec.games == null` → every
  // Situational cell rendered "—" even though the DB had real 5-5, 6-4
  // records for every team + filter + market. Now: compute the total
  // from the actual columns and hide when it's 0.
  const w = Number(rec?.wins) || 0;
  const l = Number(rec?.losses) || 0;
  const p = Number(rec?.pushes) || 0;
  const games = w + l + p;
  if (!rec || games === 0) {
    // 2026-09-26: "Cleveland's HOME tile is an empty dashed box on all
    // three tabs with no explanation. If both their games were road
    // games, a 'no home games yet' label would say so." A bare dash
    // reads as broken data; naming the reason reads as a fact.
    const _why = emptyNote
      ? `no ${String(emptyNote).toLowerCase()} games yet`
      : '—';
    return (
      <View style={sitStyles.pillEmpty}>
        <Text style={[sitStyles.pillEmptyText, emptyNote ? {fontSize: 9} : null]}>
          {_why}
        </Text>
      </View>
    );
  }
  const total = w + l;  // pushes excluded from hit%
  const hitPct = total > 0 ? Math.round((w / total) * 100) : 0;
  // 2026-09-25: football pills were ALWAYS dark. Andy: "for situational in
  // NFL boxes arent green and red ... in MLB the boxes are red and green
  // probably should be uniform across all sports."
  //
  // Cause was the flat `total >= 5` floor below. MLB teams sit at 60-140
  // games per filter so every cell clears it; football in Week 3 does not.
  // Measured against team_situational_records season=2026, market=spread:
  //
  //   MLB    overall median n=141   30/30  cells clear n>=5   -> colored
  //   NFL    overall median n=2      0/32  cells clear n>=5   -> all dark
  //   NCAAF  overall median n=3      0/182 cells clear n>=5   -> all dark
  //
  // Not one football cell out of 1,000+ could ever be coloured. It was a
  // baseball threshold applied to every sport, not a football bug.
  //
  // Fix is one rule for all sports rather than a per-sport floor: below
  // n=7 require a MARGIN of 2+ games, above it use the hit-% bands. A 3-0
  // sweep colours, 2-1 stays neutral — so small samples only speak when
  // they're lopsided, and nothing colours off a single game. The two rules
  // agree exactly where they meet (n=6: 4-2 is both margin-2 and 66.7%),
  // so there's no visible jump as the season fills in.
  // ══ 2026-09-25 · THE COMPARISON IS AUTHORITATIVE ══
  // SitRow always passes `edge` (possibly null, meaning "compared, and the
  // two teams are too close to separate"). When it does, that verdict wins
  // outright and a null means NEUTRAL — deliberately not a fall-through to
  // the absolute rule below. Falling through is what produced the report
  // in the first place: one pill lit by its own record while the pill
  // beside it stayed grey, which reads as broken rather than as "even".
  //
  // The absolute rule is kept strictly for a pill rendered with no
  // counterpart (edge === undefined). Nothing does that today; it exists so
  // reuse elsewhere degrades sensibly instead of rendering colourless.
  const MARGIN_REGIME_MAX = 6;   // n<=6 -> margin rule; n>6 -> hit-% rule
  const tint: 'win'|'loss'|'neutral'|'even' =
    edge !== undefined
      ? (edge === 'good' ? 'win' : edge === 'bad' ? 'loss'
         : edge === 'even' ? 'even' : 'neutral')
      : total < 2 ? 'neutral'
      : total <= MARGIN_REGIME_MAX
        ? (w - l >= 2 ? 'win' : l - w >= 2 ? 'loss' : 'neutral')
        : (hitPct >= 58 ? 'win' : hitPct <= 42 ? 'loss' : 'neutral');
  // 2026-09-02: label the display so users know what W-L means per market.
  //   total: "O 6 · U 4" (over/under, prevents "6-4" ambiguity from user report)
  //   spread: "6-4 ATS" (against the spread)
  //   ml: "6-4" (win/loss — self-evident)
  const label = market === 'total'
    ? `O ${w} · U ${l}${p ? ` · P ${p}` : ''}`
    : market === 'spread'
    ? `${w}-${l}${p ? `-${p}` : ''} ATS`
    // 2026-09-26: the moneyline row was the only one with no unit, so
    // "1-1" sat in a column where its neighbours read "1-1 ATS" and
    // "O 2 · U 0" and could be read as either. SU (straight up) names it.
    : `${w}-${l}${p ? `-${p}` : ''} SU`;
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
      tint === 'even' && sitStyles.pillEven,
    ]}>
      {/* ══ 2026-09-28 · THE "=" READ AS A TYPO, NOT A MEANING ══
          Andy on FLA @ CAR: "30-48 ATS = is in one block why = ... The one
          with = at the end dont have color coordinated."

          Both halves of that were fair. The glyph sat INSIDE the pill right
          after the record, so "30-48 ATS  =" looked like a malformed number
          rather than a verdict, and 'even' renders on C.surfaceAlt — a
          deliberate neutral, because neither team IS better, but
          indistinguishable from the grey of "no data" to anyone reading the
          card rather than the source.

          The comparison itself is right and stays: Florida 30-48 is 38.5%
          ATS against Carolina's 41-53 at 43.6%, a 5.1pp gap under the 15pp
          SIT_EDGE_MIN, so on ~80 games each they genuinely are not
          separable. So the fix is to stop encoding that in the number and
          say it in words underneath, which is what SitRow's existing
          "too few games to call an edge" note already does for the other
          non-verdict. One number, one place — a pill shows a record; the
          note carries the verdict. */}
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
  // 2026-09-26: "compared, and level" — visibly distinct from an empty
  // cell, without claiming either side is better.
  pillEven: {backgroundColor: C.surfaceAlt, borderWidth: 1, borderColor: C.borderSoft},
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
function TeamStatsCard({sport, homeTeam, awayTeam, season, statsSource}: any) {
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
      // 2026-09-07: per-STAT prior-season fallback (was per-team). NCAAF
      // early-season pulls populate advanced metrics (SP+, EPA, success%)
      // via CFBD /stats/season/advanced but NOT volumetric (pass_yards,
      // rush_yards, turnovers, penalty_yards) until enough games are
      // played. Old logic only fell back when BOTH teams had ZERO rows —
      // Florida State at Week 2 has 11 rows (advanced only) so fallback
      // never triggered → pass_yds_pg / rush_yds_pg / total_yds_pg
      // silently missing from the card. Now: pull both seasons, prefer
      // current-season row per stat_key, borrow prior-season row for
      // stats missing this season. Same season pool label shows in hint.
      const prevSeason = seasonForQuery > 2020 ? seasonForQuery - 1 : null;
      const seasonsToFetch = prevSeason ? [seasonForQuery, prevSeason] : [seasonForQuery];
      const [awayR, homeR] = await Promise.all([
        client.from('team_stats_rolling')
          .select('*').eq('sport', sport).eq('team', awayTeam).in('season', seasonsToFetch),
        client.from('team_stats_rolling')
          .select('*').eq('sport', sport).eq('team', homeTeam).in('season', seasonsToFetch),
      ]);
      if (cancelled) return;
      const _mergePreferring = (rows: any[]): any[] => {
        const byKey: Record<string, any> = {};
        for (const r of (rows || [])) {
          const k = r.stat_key;
          const isCurrent = Number(r.season) === seasonForQuery;
          if (!byKey[k] || isCurrent) byKey[k] = r;
        }
        return Object.values(byKey);
      };
      const ar = _mergePreferring(Array.isArray(awayR?.data) ? awayR.data : []);
      const hr = _mergePreferring(Array.isArray(homeR?.data) ? homeR.data : []);
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
  // 2026-09-26: SOS/SOR appended for every sport — schedule context
  // belongs next to the raw stats it should be read against.
  const NCAAF_OFFENSE = [
    'sos', 'sor',
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
    'sos', 'sor',
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
    'sos', 'sor',
    'ppg_for', 'off_rating', 'net_rating', 'avg_margin', 'tempo',
  ];
  const NCAAB_DEFENSE = [
    'ppg_against', 'def_rating',
  ];
  // MLB — batting from mlb_team_offense; pitching from mlb_team_pitching
  // (persisted 2026-09-01, populated by mlb_team_pitching_pull.py) + bullpen.
  const MLB_OFFENSE = [
    'sos', 'sor',
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
    'sos', 'sor',
    'points_pg', 'net_pts_pg', 'pace',
    'efg_pct', 'tov_pct', 'orb_pct', 'ft_rate',
  ];
  const NBA_DEFENSE = [
    'points_allowed_pg', 'opp_efg_pct', 'opp_tov_pct', 'opp_orb_pct',
  ];
  // NHL — expected-goal + special teams + possession
  const NHL_OFFENSE = [
    'sos', 'sor',
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
      {/* ══ 2026-09-26 · SAY WHICH SEASON THE RATING IS FROM ══
          Andy: "how does SP move that much if there is no game going on?"
          It did not move — the SOURCE changed underneath it. ncaaf_game_context
          served prior_season_regressed through 09-19 and current from 09-24,
          with no games in between:

            Akron  -6.73 (last season, shrunk toward the mean) -> -20.8 (real)
            UNLV    2.37 (same)                                ->  -3.3 (real)

          A 14-point jump that is not form, it is the regression coming off.
          ctx already records stats_source; the card just never showed it.
          Same pattern as the situational prior-season badge. */}
      {(spOvrH || spOvrA) && (
        <View>
          {statsSource && statsSource !== 'current' ? (
            <View style={{flexDirection: 'row', alignItems: 'center', gap: 6, paddingBottom: 6}}>
              <View style={{backgroundColor: C.warnDim, borderRadius: 4,
                            paddingVertical: 2, paddingHorizontal: 6}}>
                <Text style={{color: C.warn, fontSize: 9, fontWeight: '700', letterSpacing: 0.04}}>
                  LAST SEASON
                </Text>
              </View>
              <Text style={{color: C.textDim, fontSize: 10}}>
                too few games this year — prior-season rating, pulled toward average
              </Text>
            </View>
          ) : null}
          <View style={tsStyles.spBanner}>
            <View style={tsStyles.spSide}>
              <Explainer term="SP+" color={C.textMuted} activeColor={C.accent}
                         helpColor={C.text} helpBg={C.accent + '18'}>
                {/* 2026-09-26: literal glyph removed — Explainer renders its
                    own affordance, so this printed "UNLV SP+ ⓘ ⓘ". */}
                <Text style={tsStyles.spLabel}>{abbrev3(awayTeam)} SP+</Text>
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
              {/* 2026-09-26: home side had no Explainer while away did, so the
                  tap affordance appeared on one team only. */}
              <Explainer term="SP+" color={C.textMuted} activeColor={C.accent}
                         helpColor={C.text} helpBg={C.accent + '18'}>
                <Text style={tsStyles.spLabel}>{abbrev3(homeTeam)} SP+</Text>
              </Explainer>
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

      {/* Stat rows — 2026-09-16: hide rows where BOTH teams have no value
          (raw_value null on both sides). Prevents a stack of "— label —"
          empty rows on games where CFBD hasn't populated volumetric stats
          yet. Row still renders if at least one team has data — asymmetric
          info (e.g., FBS opponent stat vs FCS blank) still surfaces. */}
      {statKeys.map(k => {
        const a = awayByKey[k]; const h = homeByKey[k];
        const bothEmpty = (!a || a.raw_value == null) && (!h || h.raw_value == null);
        if (bothEmpty) return null;
        return <StatRow key={k} statKey={k} awayRow={a} homeRow={h} />;
      })}

      {loading && <Text style={rsStyles.empty}>Loading…</Text>}
    </View>
  );
}

// 2026-09-24 ADVANTAGE COLOURING. Andy: "if the other team is better the
// stat green and the other team stat red ... I want users to be able to
// readily identify which team is better in any given aspect, across all
// sports."
//
// Compared on RANK, not raw value, for two reasons. Rank is already
// polarity-aware everywhere — RankChip colours by rank/league_size with
// the top quintile green, so rank 1 is the best team at that stat whether
// the stat is points scored or points allowed or strikeout rate. That
// means this needs no per-stat higher_is_better table and works for every
// sport the moment the sport populates team_stats_rolling. And rank
// carries context a raw value does not: .241 vs .254 is 18th vs 4th,
// which is the thing worth seeing.
//
// A neutral band is deliberate. Colouring every row would claim an edge
// on gaps that are noise, and a screen where everything is coloured says
// nothing. Under 5 places apart, neither side is tinted.
// Percentile gaps, not raw rank gaps — see advantage(). 9% of a 134-team
// league is ~12 places, which is what these used to mean for FBS; they now
// mean the same thing in every universe instead of only that one.
const ADV_STRONG = 0.09;  // clear edge — colour plus weight
const ADV_SLIGHT = 0.04;  // visible edge — colour only

// 2026-09-25: was a raw rank gap, which is not comparable across rows.
// league_size differs BY STAT (139 for SP+, 216 for most defensive rates,
// 266 for offensive rates), so a 12-place gap is a big edge in a 139-team
// universe and noise in a 266-team one — the same defect that made the
// rank chips unreadable. Thresholds are now percentile gaps, so one rule
// holds for every stat and every sport.
function advantage(aRank: any, bRank: any,
                   leagueSize?: any): 'strong' | 'slight' | null {
  if (aRank == null || bRank == null) return null;
  const size = Number(leagueSize);
  const gap = Math.abs(Number(aRank) - Number(bRank));
  if (!isFinite(gap)) return null;
  // Fall back to the old absolute gap only when league_size is missing.
  const pctGap = isFinite(size) && size > 0 ? gap / size : gap / 134;
  if (pctGap >= ADV_STRONG) return 'strong';
  if (pctGap >= ADV_SLIGHT) return 'slight';
  return null;
}

// ══ 2026-09-26 · PER-STAT INFO ══
// Andy: "there should be info things in each stat to explain to users what
// they mean" / "an info button on the stat in the card detail to explain
// what the stat means and what the number means."
//
// Deliberately NOT routed through the Explainer glossary. A glossary answers
// "what is EPA"; it cannot answer "is 0.446 good", which is the half that
// actually decides a bet. Each entry carries both: what the metric is, and
// how to read the number in front of you, with a real anchor value so the
// scale means something.
//
// `hi` names which direction is better, so the copy can state it rather than
// leaving the user to infer it from the colour.
const INFO_GLYPH = 'ⓘ';
const STAT_INFO: Record<string, {name: string; what: string; read: string}> = {
  // 2026-09-26: SOS and SOR are the two people conflate most, so the copy
  // leads with the difference rather than a definition.
  // 2026-09-26 · ⓘ COVERAGE FOR EVERY SPORT.
  // Andy: "want to make sure that stat info clicking was spread to every
  // sport to ensure user understands significance of each stat." Audited
  // stat_key by stat_key against team_stats_rolling: NCAAF was complete
  // at 18/18, but NFL had 7 of 18 and MLB 2 of 21, while NBA, NHL and
  // NCAAB were bare — 55 stats rendered a value and a percentile with no
  // way to learn what either meant. Each entry says what the number is
  // and how to read it for a bet, because "Team BAA .247" tells a casual
  // bettor nothing on its own.

  // ── NFL ──
  off_pass_epa: {name: 'Off Pass EPA',
    what: 'Expected points added per dropback — how much each pass play improves the offense’s scoring position.',
    read: 'Above 0.15 is a real passing offense; negative means dropbacks are actively hurting them. The most predictive offensive number in football, more so than passing yards.'},
  off_rush_epa: {name: 'Off Rush EPA',
    what: 'Expected points added per rush attempt.',
    read: 'Rushing EPA sits near zero even for good teams, so small edges matter. Read it against the opponent’s Def Rush EPA — a positive rush offense against a leaky run defence is the classic script-control spot.'},
  def_pass_epa: {name: 'Def Pass EPA',
    what: 'Expected points allowed per opponent dropback. Lower is better.',
    read: 'Negative means the pass defence takes points off the board. Pair it with the opponent’s Off Pass EPA — a big gap moves a total more reliably than either rushing number.'},
  def_rush_epa: {name: 'Def Rush EPA',
    what: 'Expected points allowed per opponent rush. Lower is better.',
    read: 'A strong run defence forces one-dimensional offences, which shows up in the under and in second-half lines more than in the side.'},
  pass_tds_pg: {name: 'Pass TDs/G',
    what: 'Passing touchdowns per game.',
    read: 'Touchdown rate is noisier than yardage and regresses hard. A team far above its yardage rank here is likely to cool off — useful as a fade signal on totals.'},
  rush_tds_pg: {name: 'Rush TDs/G',
    what: 'Rushing touchdowns per game.',
    read: 'Driven by goal-line volume, so it says as much about how often a team reaches the red zone as how well it runs.'},
  ints_pg: {name: 'INTs Thrown/G',
    what: 'Interceptions thrown per game. Lower is better.',
    read: 'The most volatile stat on this card. One multi-pick game distorts it all season and it regresses more than almost any other number — do not fade a team on this alone.'},
  sacks_suffered_pg: {name: 'Sacks Allowed/G',
    what: 'Times the quarterback is sacked per game. Lower is better.',
    read: 'Part offensive line, part how long the QB holds the ball. Against a heavy pass rush it is a leading indicator of stalled drives and the under.'},
  pass_yds_allowed_pg: {name: 'Pass Yds Allowed/G',
    what: 'Opponent passing yards per game. Lower is better.',
    read: 'Distorted by game script — teams that lead all game allow garbage-time passing and look worse than they are. Def Pass EPA is the cleaner read.'},
  rush_yds_allowed_pg: {name: 'Rush Yds Allowed/G',
    what: 'Opponent rushing yards per game. Lower is better.',
    read: 'Same game-script caveat in reverse: teams that trail get run on late. Still the number to check when the opponent leans run-first.'},
  yds_allowed_pg: {name: 'Yds Allowed/G',
    what: 'Total opponent yards per game. Lower is better.',
    read: 'A volume stat, not an efficiency one. A fast-paced opponent slate inflates it — read the EPA rows alongside it, not instead of it.'},

  // ── MLB ──
  team_runs_pg: {name: 'Runs/G',
    what: 'Runs scored per game.',
    read: 'The bluntest offensive measure and the one most tied to totals. Check it against wRC+ — a gap means the runs came from sequencing luck rather than hitting.'},
  team_avg: {name: 'AVG',
    what: 'Batting average — hits divided by at-bats.',
    read: 'The weakest rate stat here: it treats a single and a home run the same and ignores walks entirely. OPS and wRC+ are far better guides.'},
  team_obp: {name: 'OBP',
    what: 'On-base percentage — how often a hitter reaches base.',
    read: 'Tracks run scoring better than average. High-OBP lineups drive pitch counts up, which matters for opposing-starter prop unders.'},
  team_slg: {name: 'SLG',
    what: 'Slugging — total bases per at-bat.',
    read: 'Measures power, not frequency. A high-SLG, low-OBP lineup is feast-or-famine, which widens the range of outcomes on a total.'},
  team_ops: {name: 'OPS',
    what: 'On-base plus slugging — reaching base and hitting for power combined.',
    read: 'The quickest single read of an offence. Around .700 is average, .800+ is strong. Not park-adjusted, so a Coors number is not a Petco number.'},
  team_iso: {name: 'ISO',
    what: 'Isolated power — slugging minus batting average, so extra bases only.',
    read: 'Strips out singles to show raw power. High ISO against a fly-ball pitcher in a hitter park is the cleanest home-run-prop setup on the card.'},
  team_woba: {name: 'wOBA',
    what: 'Weighted on-base average — every outcome weighted by the runs it is actually worth.',
    read: 'Scaled like OBP, so .320 is average and .350+ is strong. Better than OPS because it weights a double correctly instead of double-counting.'},
  team_wrc_plus: {name: 'wRC+',
    what: 'Runs created, adjusted for ballpark and league, where 100 is exactly average.',
    read: 'The best single offensive number here because the park adjustment is built in. 120 means 20% better than average; 80 means 20% worse.'},
  team_k_pct: {name: 'K%',
    what: 'Share of plate appearances ending in a strikeout. Lower is better for the offence.',
    read: 'High-strikeout lineups hand free outs to a strikeout pitcher. The first row to check before backing a pitcher strikeout over.'},
  team_bb_pct: {name: 'BB%',
    what: 'Share of plate appearances ending in a walk.',
    read: 'Patient lineups drive pitch counts and shorten starts, pushing an opposing starter toward the under on outs and strikeouts.'},
  team_hr_pg: {name: 'HR/G',
    what: 'Home runs hit per game.',
    read: 'Park and weather dependent. Read it with ISO and the park factor rather than alone — the same lineup is a different team in a different stadium.'},
  team_era: {name: 'Team ERA',
    what: 'Earned runs allowed per nine innings by the whole staff. Lower is better.',
    read: 'Backward-looking and defence-dependent. WHIP and K/9 react faster to how a staff is actually throwing right now.'},
  team_whip: {name: 'Team WHIP',
    what: 'Walks plus hits allowed per inning. Lower is better.',
    read: 'Baserunner rate. Around 1.30 is average; above 1.40 means constant traffic, which lifts run totals even when ERA still looks acceptable.'},
  team_baa: {name: 'Team BAA',
    what: 'Batting average opponents hit against this staff. Lower is better.',
    read: 'Contact suppression. A low BAA built on soft contact is repeatable; one built on defensive luck is not.'},
  team_k_per_9: {name: 'Team K/9',
    what: 'Strikeouts per nine innings. Higher is better.',
    read: 'The most repeatable pitching skill and the least dependent on the defence behind it. The strongest single input to a strikeout prop.'},
  team_bb_per_9: {name: 'Team BB/9',
    what: 'Walks per nine innings. Lower is better.',
    read: 'Free baserunners. Above 3.5 is a staff that beats itself, and it correlates with short starts — relevant to any outs-recorded prop.'},
  team_hr_per_9: {name: 'Team HR/9',
    what: 'Home runs allowed per nine innings. Lower is better.',
    read: 'The fastest way to a crooked number. Combine with the park factor and wind before trusting an under.'},
  bullpen_era: {name: 'Bullpen ERA',
    what: 'Earned runs per nine innings from relievers only. Lower is better.',
    read: 'Decides late-game overs and first-five bets. A good rotation with a poor bullpen is the classic F5-under, full-game-over profile.'},
  bullpen_save_pct: {name: 'Bullpen Save %',
    what: 'Share of save chances the bullpen converts.',
    read: 'Matters most for moneyline favourites and run-line bets — a leaky pen turns comfortable leads into live underdog spots.'},

  // ── NBA ──
  points_pg: {name: 'Points/G',
    what: 'Points scored per game.',
    read: 'Pace-inflated: a fast team scores more without being better. Read it with Pace, or use Off Rating which already adjusts for possessions.'},
  net_pts_pg: {name: 'Net Pts/G',
    what: 'Average scoring margin — points scored minus points allowed.',
    read: 'Predicts future results better than win-loss record. A team well above its record here has been unlucky and is usually worth backing.'},
  pace: {name: 'Pace',
    what: 'Possessions per 48 minutes.',
    read: 'A totals input, not a quality one. Two fast teams raise the total regardless of how good either is; fast against slow usually lands in between.'},
  efg_pct: {name: 'eFG %',
    what: 'Field goal percentage that counts a three as worth more than a two.',
    read: 'The largest of the four factors and the biggest single driver of winning. Around 53% is average.'},
  opp_efg_pct: {name: 'Opp eFG %',
    what: 'Effective field goal percentage allowed. Lower is better.',
    read: 'The clearest measure of defensive quality — shot quality allowed is far more repeatable than raw points allowed, which is pace-distorted.'},
  tov_pct: {name: 'TOV %',
    what: 'Share of possessions ending in a turnover. Lower is better.',
    read: 'Every turnover is a possession worth zero points. High-turnover teams underperform their shooting and are vulnerable to pressure defences.'},
  opp_tov_pct: {name: 'Opp TOV % (D forced)',
    what: 'Share of opponent possessions this defence forces into a turnover. Higher is better.',
    read: 'Forced turnovers create transition offence, so a team strong here often scores more than its half-court efficiency suggests.'},
  orb_pct: {name: 'ORB %',
    what: 'Share of available offensive rebounds collected.',
    read: 'Second chances. Strong offensive rebounding buys extra possessions, which lifts the total even when shooting is ordinary.'},
  ft_rate: {name: 'FT Rate',
    what: 'Free throw attempts relative to field goal attempts.',
    read: 'Rim pressure, and a total-lifter because free throws stop the clock. Teams that live at the line are less affected by a cold shooting night.'},
  opp_ft_rate: {name: 'Opp FT Rate',
    what: 'Free throw attempts conceded, relative to field goal attempts. Lower is better.',
    read: 'A foul-prone defence gives away the most efficient shot in basketball and risks key players in foul trouble.'},
  wins: {name: 'Wins',
    what: 'Games won this season.',
    read: 'Record alone hides strength of schedule and luck in close games. Net Pts/G is the better predictor of what happens next.'},

  // ── NHL ──
  corsi_5v5: {name: '5v5 CF %',
    what: 'Share of all even-strength shot attempts that belong to this team.',
    read: 'The standard possession proxy. Above 52% is genuine territorial control, and it predicts future results better than goals because it is far less noisy.'},
  xgf_per60: {name: 'xGF/60',
    what: 'Expected goals generated per 60 minutes, from the quality and location of chances created.',
    read: 'Rewards dangerous chances rather than shot volume. A team scoring below its xGF is usually due for positive regression, not broken.'},
  xga_per60: {name: 'xGA/60',
    what: 'Expected goals conceded per 60 minutes. Lower is better.',
    read: 'Measures the defence in front of the goalie. A team with good xGA and bad results has a goaltending problem, not a defensive one.'},
  high_danger_for: {name: 'HD Chances For',
    what: 'High-danger scoring chances created, mostly shots from the slot.',
    read: 'Where goals actually come from. A team generating volume but few high-danger looks tends to underperform its shot totals.'},
  high_danger_against: {name: 'HD Chances Against',
    what: 'High-danger chances conceded. Lower is better.',
    read: 'The clearest under signal on this card — a defence that keeps opponents to the perimeter suppresses goals even against good offences.'},
  pp_pct: {name: 'PP %',
    what: 'Power play conversion rate.',
    read: 'Special teams swing tight games, and power play rate is volatile in small samples. Above 25% is elite, but check the sample before leaning on it.'},
  pk_pct: {name: 'PK %',
    what: 'Penalty kill success rate.',
    read: 'Matters most against a strong power play and in tightly officiated games. Below 75% is a genuine liability.'},

  // ── NCAAB ──
  off_rating: {name: 'Off Rating',
    what: 'Points scored per 100 possessions.',
    read: 'Pace-adjusted, so it compares a fast team and a slow team fairly — which raw points per game cannot do.'},
  def_rating: {name: 'Def Rating',
    what: 'Points allowed per 100 possessions. Lower is better.',
    read: 'The honest measure of a defence. A slow team allowing few points per game may just be limiting possessions rather than defending well.'},
  net_rating: {name: 'Net Rating',
    what: 'Offensive rating minus defensive rating, per 100 possessions.',
    read: 'The best single number on a college basketball card. The gap between two teams here is a rough pre-adjustment point spread.'},
  tempo: {name: 'Tempo',
    what: 'Possessions per 40 minutes.',
    read: 'A totals input, not a quality one. College tempo varies far more than the NBA, so a slow team can drag a fast opponent into the low 60s.'},
  ppg_for: {name: 'Points/G',
    what: 'Points scored per game.',
    read: 'Tempo-inflated. Use Off Rating for quality and keep this for the total.'},
  ppg_against: {name: 'Points Allowed/G',
    what: 'Points conceded per game. Lower is better.',
    read: 'Tempo-inflated in reverse — a slow team looks like a good defence. Def Rating is the row to trust.'},
  avg_margin: {name: 'Avg Margin',
    what: 'Average winning or losing margin.',
    read: 'Predicts future performance better than record, but is distorted by blowouts against weak non-conference opponents. Read it next to Strength of Schedule.'},

  // 2026-10-09: these two now carry the OPPONENT-ADJUSTED MARGIN rating, not
  // opponent win rate. The values are in POINTS and the help text below was
  // rewritten to match — the previous copy described a win-share fraction and
  // would have been actively wrong about what the number on screen means.
  // Backend swap only (compute_margin_strength now writes the sos/sor keys),
  // because there is no OTA path and the card's stat_key list is hardcoded.
  sos: {name: 'Strength of Schedule',
        what: 'How hard the opponents this team has already played are, in points — the average quality of the schedule faced, where 0 is an average opponent.',
        read: 'Higher means a tougher slate. It says nothing about how good THIS team is: a 1-4 team can lead the league in it. Use it to judge whether a record was earned or inherited. Because it rates opponents on scoring margin adjusted for who THEY played — not just their win-loss record — a team that beat someone who later turns out to be good gets credit for it as that opponent\'s rating rises.'},
  sor: {name: 'Strength of Record',
        what: 'How good this team has actually been, in points, once you correct for who they played — roughly how much better or worse than an average team they would be on a neutral field.',
        read: '+10 is a strong team, 0 is average, -10 a weak one, and the gap between two teams is roughly the spread you would expect on a neutral field. This is the one that separates teams; SOS alone does not. It is built from scoring margin rather than win-loss, with home field, blowout damping and a correction for how few games have been played, so one lopsided result cannot carry it.'},
  sp_overall:  {name: 'SP+ Overall', what: 'A tempo- and opponent-adjusted rating of overall team quality, in points.',
                read: 'It is a points-above-average figure, so 0 is an average team. +10 is a strong team, -10 a weak one. The gap between two teams is roughly the spread on a neutral field.'},
  sp_offense:  {name: 'SP+ Offense', what: 'The offensive half of SP+ — points the offense is worth against an average defense.',
                read: 'Higher is better. Around 26 is average; low teens is a bottom-tier offense.'},
  sp_defense:  {name: 'SP+ Defense', what: 'The defensive half of SP+ — points allowed against an average offense.',
                read: 'LOWER is better here, which is the opposite of the offense number. Around 26 is average; 33+ is a leaky defense.'},
  off_epa_per_play: {name: 'Offensive EPA / Play', what: 'Expected Points Added per snap — how much each play moves the scoreboard forecast.',
                read: 'Per-play, so the numbers are small. Above +0.10 is very good, 0.00 is average, below -0.05 is poor.'},
  def_epa_per_play: {name: 'Defensive EPA Allowed', what: 'Expected Points Added the defense gives up per opposing snap.',
                read: 'LOWER is better. Below 0.00 means the defense is taking points off the board; above +0.15 is being moved at will.'},
  def_rush_epa_allowed: {name: 'Defensive Rush EPA', what: 'Expected Points Added allowed per opposing run.',
                read: 'Lower is better. 0.00 is a solid run defense; above +0.10 means runs are consistently gaining real value.'},
  off_success_rate: {name: 'Offensive Success Rate', what: 'Share of plays that gain enough yardage to stay on schedule for a first down.',
                read: 'Higher is better. Around 43% is average; 48%+ is an efficient offense that rarely faces long third downs.'},
  def_success_rate_allowed: {name: 'Success Rate Allowed', what: 'Share of opposing plays that stayed on schedule.',
                read: 'LOWER is better. Under 40% is a defense that gets teams behind the sticks; 46%+ means opponents move freely.'},
  off_explosiveness: {name: 'Offensive Explosiveness', what: 'Average value of the plays that DO succeed — big-play punch rather than frequency.',
                read: 'Higher is better. Around 1.20 is average. A low number with a decent success rate means an offense that grinds but never breaks one.'},
  points_allowed_pg: {name: 'Points Allowed / Game', what: 'Average points conceded per game this season.',
                read: 'Lower is better. Raw and unadjusted, so a soft schedule flatters it — check SP+ Defense alongside it.'},
  pass_yds_pg: {name: 'Pass Yards / Game', what: 'Average passing yards gained per game.',
                read: 'Volume, not efficiency. A high number can just mean a team trails often and throws to catch up.'},
  rush_yds_pg: {name: 'Rush Yards / Game', what: 'Average rushing yards gained per game.',
                read: 'Volume, not efficiency. Teams that lead tend to run more, so this partly measures game script.'},
  total_yds_pg: {name: 'Total Yards / Game', what: 'Combined pass and rush yards per game.',
                read: 'Volume. Two teams can share a number with very different efficiency — EPA/play separates them.'},
  third_down_pct: {name: 'Third Down %', what: 'Share of third downs converted into a first down.',
                read: 'Higher is better. Around 40% is average. It is noisy early in a season — a few plays swing it several points.'},
  turnovers_pg: {name: 'Turnovers / Game', what: 'Giveaways per game.',
                read: 'Lower is better, and this is the least sticky number on the card — turnover rates regress hard, so do not weight it like the efficiency stats.'},
  penalty_yds_pg: {name: 'Penalty Yards / Game', what: 'Penalty yardage conceded per game.',
                read: 'Lower is better, but the effect on a result is small relative to efficiency.'},
  def_pass_ypg: {name: 'Pass Yards Allowed / Game', what: 'Average passing yards conceded per game.',
                read: 'Lower looks better but is misleading on its own — teams that lead get passed on more. Pair it with Defensive EPA.'},
  def_rush_ypg: {name: 'Rush Yards Allowed / Game', what: 'Average rushing yards conceded per game.',
                read: 'Lower looks better, with the same caveat: teams that trail get run on late.'},
};

function StatInfoRow({info, onClose}: any) {
  return (
    <View style={{
      backgroundColor: C.surfaceAlt, borderRadius: 8, padding: 10, gap: 4,
      borderLeftWidth: 3, borderLeftColor: C.accent, marginTop: 2,
    }}>
      <Text style={{color: C.text, fontSize: 12, fontWeight: '800'}}>{info.name}</Text>
      <Text style={{color: C.textDim, fontSize: 11, lineHeight: 16}}>{info.what}</Text>
      <Text style={{color: C.text, fontSize: 11, lineHeight: 16}}>{info.read}</Text>
      <TouchableOpacity onPress={onClose} activeOpacity={0.7} style={{alignSelf: 'flex-start', paddingTop: 2}}>
        <Text style={{color: C.accent, fontSize: 10, fontWeight: '700'}}>Close</Text>
      </TouchableOpacity>
    </View>
  );
}

function StatRow({statKey, awayRow, homeRow}: any) {
  // Prefer whichever has display_label present (both should have same);
  // fall back to prettified stat_key.
  const label = awayRow?.display_label || homeRow?.display_label
             || statKey.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase());
  const unit = awayRow?.unit || homeRow?.unit || '';
  // Lower rank is better. Only assign an edge when both sides are ranked.
  const info = STAT_INFO[statKey];
  const [showInfo, setShowInfo] = React.useState(false);
  const tier = advantage(awayRow?.rank, homeRow?.rank,
                         awayRow?.league_size || homeRow?.league_size);
  const awayBetter = tier != null && Number(awayRow.rank) < Number(homeRow.rank);

  // 2026-09-26 · A TIE MUST READ AS A TIE.
  //
  // Andy has now reported the same shape five times — "both teams show
  // byte-identical values and percentiles on two consecutive rows ...
  // something is mirroring one column into both." Checked by hand on
  // Oregon State @ UTEP and the numbers are genuinely equal: both teams
  // played one undefeated opponent, one opponent with no other result,
  // and one 2-1 opponent, so strength of schedule lands on 0.750 for
  // each. Not a mirror — a real coincidence.
  //
  // But it will keep happening and it will keep looking broken, because
  // early in a season these metrics are extremely coarse: measured on
  // 2026-09-26, NCAAF sos had 21 distinct values across 152 teams and
  // turnovers_pg had 27 across 216. Identical columns are the expected
  // outcome of a small sample, not evidence of a bug.
  //
  // So say so on the row. An explicit "=" is how the situational card
  // already marks a tie, and reusing it means an identical pair reads as
  // "these are level" instead of "this screen is duplicating data".
  const _tied = (awayRow?.raw_value != null && homeRow?.raw_value != null
                 && Number(awayRow.raw_value) === Number(homeRow.raw_value));
  // One precision for the whole row — see rowDecimals. Computed here rather
  // than inside each StatCell so the two halves cannot disagree.
  const _dp = rowDecimals(awayRow?.raw_value, homeRow?.raw_value, statKey);
  // 2026-09-26: the stat NAME is the info affordance. Tapping it explains
  // the metric and, more usefully, how to read the number — "is 0.446 good"
  // is the question a rank alone never answers. Attached to the label rather
  // than a separate icon so the row does not gain a third glyph and the tap
  // target is the whole name.
  return (
    <View>
      <View style={tsStyles.statRow}>
        <StatCell row={awayRow} unit={unit} align="right" decimals={_dp}
                  edge={tier ? (awayBetter ? 'good' : 'bad') : null} strong={tier === 'strong'} />
        {info ? (
          <TouchableOpacity style={{flex: 1}} activeOpacity={0.6}
                            onPress={() => setShowInfo(v => !v)}>
            {/* 2026-09-26: had textDecorationLine:'underline' +
                textDecorationStyle:'dotted', which RN resolves to
                LINE-THROUGH on iOS — all nine labels rendered struck out,
                so the whole table read as retracted. The ⓘ is affordance
                enough. numberOfLines caps the wrap that was splitting
                "OFF EXPLOSIVENES / S" mid-word and knocking that row's
                baseline out of line with the rest. */}
            <Text style={tsStyles.statLabel} numberOfLines={2}>
              {label} <Text style={{color: C.accent, fontSize: 10}}>{INFO_GLYPH}</Text>
              {_tied ? <Text style={{color: C.textDim, fontSize: 10}}>{'  ='}</Text> : null}
            </Text>
          </TouchableOpacity>
        ) : (
          <Text style={tsStyles.statLabel}>
            {label}
            {_tied ? <Text style={{color: C.textDim, fontSize: 10}}>{'  ='}</Text> : null}
          </Text>
        )}
        <StatCell row={homeRow} unit={unit} align="left" decimals={_dp}
                  edge={tier ? (awayBetter ? 'bad' : 'good') : null} strong={tier === 'strong'} />
      </View>
      {showInfo && info ? (
        <StatInfoRow info={info} onClose={() => setShowInfo(false)} />
      ) : null}
    </View>
  );
}

function StatCell({row, unit, align, edge, strong, decimals}: any) {
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
        // Shrink rather than overflow — see the statCell/statRow notes.
        minWidth: 0, flexShrink: 1,
      }}>
        {/* 2026-09-01: split value + unit into sibling Text components
            instead of nesting. Nested Text inside a parent Text can lose
            explicit color inheritance in RN under certain style-array
            combinations, resulting in default-black text. Siblings each
            hold their own StyleSheet reference so color is always
            explicit. */}
        {/* ══ 2026-09-26 · TWO SIGNALS, TWO CHANNELS ══
            Colour used to mean head-to-head while the pill beside it
            states the absolute standing, and once the pill became a plain
            percentile the conflict was unmissable: on CSU @ UTSA a
            70th-percentile offence rendered RED while three below-median
            rows rendered GREEN. Both were "true" and together they were
            nonsense.

            Colour now comes from the PERCENTILE — it agrees with the pill
            by construction. The head-to-head edge moves to a small
            triangle on the better side, so the two facts never compete
            for the same channel. Andy's suggestion, and it is the right
            one. */}
        <Text style={[
          tsStyles.statValue,
          _pctileColor(row.rank, row.league_size),
          strong && edge ? {fontWeight: '800' as const} : null,
        ]}>{fmtStatValue(row.raw_value, decimals)}</Text>
        {edge === 'good' ? (
          <Text style={{color: C.win, fontSize: 10, fontWeight: '800'}}>{'\u25B2'}</Text>
        ) : edge === 'bad' ? (
          <Text style={{color: C.textMuted, fontSize: 10, fontWeight: '800'}}>{'\u25BC'}</Text>
        ) : null}
        {unit ? <Text style={tsStyles.statUnit}> {unit}</Text> : null}
        <RankChip rank={row.rank} leagueSize={row.league_size} />
      </View>
    </View>
  );
}

// 2026-09-25: Akron's Def Rush EPA rendered as a bare "0" beside UNLV's
// "0.126" — the value really is 0.0, but printing it raw made a legitimate
// number look like missing data. Decimals now follow magnitude, so every
// cell in a row is written to the same precision: per-play/EPA-scale values
// (|v| < 2) get 3 dp, rate and per-game values get 1, counting stats get 0.
// ══ 2026-09-27 · PRECISION IS A PROPERTY OF THE STAT, NOT OF THE VALUE ══
//
// Andy, on BAL @ DAL: "Decimal precision is inconsistent: 1.000 vs 3.0,
// 2.5 vs 0.500, 2.5 vs 1.000."
//
// The comment above claimed "every cell in a row is written to the same
// precision" and the code did the opposite — it chose decimals from EACH
// CELL'S OWN magnitude, so the two halves of one row disagreed whenever
// they straddled a threshold:
//     PASS TDS/G      BAL 1.000   DAL 3.0     (1.0 < 2 <= 3.0)
//     RUSH TDS/G      BAL 2.5     DAL 0.500
//     SACKS ALLOWED/G BAL 2.5     DAL 1.000
// Reading those as the same measurement takes real effort, and 1.000 next
// to 3.0 implies the left number was measured three digits more finely.
//
// Decimals are now decided ONCE PER ROW from the larger of the two values,
// then applied to both cells. `decimals` is threaded in from the row rather
// than recomputed per cell so the two can't drift again.
const _LOW_RES_STATS = new Set(['sos', 'sor']);

function rowDecimals(a: any, b: any, statKey?: string): number {
  // SOS/SOR are held at two decimals, but as of 2026-10-09 for a DIFFERENT
  // reason than when this was written. The original: they were win-share
  // fractions over a handful of games and genuinely coarse — measured
  // 2026-09-27, NFL sos had 7 distinct values across 32 teams and sor had 6,
  // so three decimals on "0.000" advertised precision the metric did not
  // have. They now carry an opponent-adjusted margin in POINTS, which is
  // continuous, so the ties-as-placeholder problem is gone — but two
  // decimals is still right for a points figure, where "+2.53" is readable
  // and "+2.534" is false precision on a few games of scoring margin.
  if (statKey && _LOW_RES_STATS.has(statKey)) return 2;
  const mag = Math.max(
    a == null || !isFinite(Number(a)) ? 0 : Math.abs(Number(a)),
    b == null || !isFinite(Number(b)) ? 0 : Math.abs(Number(b)),
  );
  if (mag < 2)   return 3;   // per-play / EPA scale
  if (mag < 100) return 1;   // per-game rates and counts
  return 0;                  // yardage and other volume totals
}

function fmtStatValue(v: any, decimals?: number): string {
  if (v == null) return '—';
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  if (decimals != null) return n.toFixed(decimals);
  // Fallback for any caller that has no row context — same thresholds as
  // before so nothing changes shape unexpectedly.
  const a = Math.abs(n);
  if (a < 2)   return n.toFixed(3);
  if (a < 100) return n.toFixed(1);
  return n.toFixed(0);
}

// 2026-09-26: colour keyed to the SAME percentile the chip prints, so the
// number and the pill can never disagree. Neutral band kept wide — most
// teams are unremarkable and colouring them implies a verdict we do not
// have.
function _pctileColor(rank: any, leagueSize: any): any {
  const r = Number(rank), n = Number(leagueSize);
  if (!isFinite(r) || !isFinite(n) || n <= 0) return null;
  const better = 1 - (r / n);          // share of teams this one beats
  if (better >= 0.70) return {color: C.win};
  if (better <= 0.30) return {color: C.loss};
  return null;
}

function RankChip({rank, leagueSize}: any) {
  if (rank == null || leagueSize == null || leagueSize === 0) return null;
  // Quintile color: top 20% = elite, next 20% = good, mid = neutral, next 20% = poor, bottom = bad
  // 2026-09-02: text stays bright cream ALWAYS. Prior version colored the
  // text by tier (win/sharp/textDim/warn/loss) — the textDim + loss options
  // rendered dark on tinted bg, matched user's "118th in dark lettering"
  // report. Now: only BG tints, text stays high-contrast. Same pattern
  // as RecordPill fix (2026-09-01).
  const pct = rank / leagueSize;
  // ══ 2026-09-26 · ONE COLOUR SYSTEM, ONE DIRECTION ══
  // Two problems Andy flagged on the same chip.
  //
  // (#10) The chip used to carry its own quintile colour while the NUMBER
  // beside it is coloured head-to-head. Two colour scales in one row, saying
  // different things: "RUSH YDS/G 172" rendered GREEN (better than Akron)
  // directly beside a RED chip meaning "bottom third of the league". Both
  // were true and together they read as a contradiction. The chip is now
  // neutral — colour in this panel means one thing only, which team has the
  // better side of this matchup. The chip's TEXT still carries the absolute
  // standing, so nothing is lost.
  //
  // (#12) "Bot 46%" and "Top 50%" are adjacent percentiles that read as
  // opposites because the label flips direction across the median. Now a
  // single upward scale: the percentile of teams this one is BETTER than.
  // 79th is good, 4th is bad, and it never inverts.
  const bg = C.surfaceAlt;
  const fg = C.text;
  // ══ 2026-09-25 · PERCENTILE, NOT A BARE ORDINAL ══
  // Andy screenshot (UNLV @ Akron, Defense): one panel showed "95th" and
  // "188th" stacked on top of each other. They are not on the same scale —
  // league_size varies BY STAT because the sources cover different team
  // universes:
  //
  //   sp_defense / sp_offense / sp_overall   139
  //   def_epa_per_play, points_allowed_pg…   216
  //   off_epa_per_play, third_down_pct…      266
  //
  // So 95th was the 32nd percentile and 188th was the 13th, and the chip
  // showed neither denominator. Worse, FBS is 134 teams, so a raw "188th"
  // reads as "worse than last place" to anyone who knows the sport — the
  // 216/266 universes include non-FBS teams.
  //
  // The percentile was already being computed right above for the colour;
  // it just wasn't the thing displayed. Showing it makes every row
  // comparable regardless of universe, and kills the sub-134 confusion.
  const better = Math.max(1, Math.min(99, Math.round((1 - pct) * 100)));
  const _sfx = (n: number) => {
    const t = n % 100;
    if (t >= 11 && t <= 13) return 'th';
    return n % 10 === 1 ? 'st' : n % 10 === 2 ? 'nd' : n % 10 === 3 ? 'rd' : 'th';
  };
  // 2026-09-26: "2nd pct" reads as "2nd best" — the ordinal fights the
  // meaning exactly at the low end, where the number matters most.
  // "%ile" is unambiguous in both directions.
  //
  // ══ 2026-09-27 · RANK IN A SMALL LEAGUE, PERCENTILE IN A BIG ONE ══
  //
  // Andy: "do we think percentile is better for users in stats and not
  // ranks? 1-32 for NFL." Rank is better at 32 teams, for three reasons
  // all visible on the BAL @ DAL card:
  //
  //   1. GRANULARITY. With 32 teams a percentile can only take 32 values,
  //      3.2pp apart. "19th %ile" looks measured to the point and is not —
  //      the same false precision as printing SOS as "0.000".
  //   2. TIES READ AS TIES. BAL and DAL both sit at rank 26/32. "26/32" on
  //      both sides is obviously one shared standing; two identical
  //      percentiles is what made Andy file this as a placeholder bug.
  //   3. NO MENTAL ARITHMETIC. "26/32" is the answer. "19th %ile" is one
  //      step away from it.
  //
  // Percentile still earns its place where the universe is large or
  // varies: NCAAF stats span 139/216/266-team universes and NCAAB 360+,
  // where "188th" reads as worse than last place (the 2026-09-25 note
  // above) and a percentile is the only comparable form.
  //
  // Keyed on league_size, NOT on sport, precisely because league_size
  // already varies BY STAT within one sport. A size rule is therefore
  // correct everywhere without plumbing a sport prop to every call site:
  // NFL/NHL 32, NBA/MLB 30 fall under the cut; the smallest NCAAF
  // universe is 139 and stays on percentile.
  // A THIRD CASE, found while making the split: league_size can be far
  // smaller than the sport's actual team count, because it counts teams
  // that HAVE the stat, not teams that exist. On 2026-09-27 seven NHL
  // advanced stats (corsi_5v5, xgf_per60, pp_pct, pk_pct, high_danger_*,
  // xga_per60) had league_size 4 — only Philadelphia, NY Rangers,
  // Pittsburgh and Ottawa have been ingested for 2026.
  //
  // "1/4" is arithmetically true and badly misleading: it reads as a
  // four-team league. A percentile would be worse, claiming 100th. So
  // below a floor the chip says what it actually is — a standing among
  // the teams we have — rather than implying a league standing. Labelled
  // rather than hidden, because a silently missing chip has repeatedly
  // been read on this screen as a fault.
  const RANK_MAX_LEAGUE = 40;
  const RANK_MIN_LEAGUE = 10;
  const label = Number(leagueSize) < RANK_MIN_LEAGUE
    ? `${rank} of ${leagueSize} ranked`
    : Number(leagueSize) <= RANK_MAX_LEAGUE
      ? `${rank}/${leagueSize}`
      : `${better}${_sfx(better)} %ile`;
  return (
    <View style={[tsStyles.rankChip, {backgroundColor: bg}]}>
      <Text style={[tsStyles.rankText, {color: fg}]}>{label}</Text>
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
  // 2026-09-27: paddingHorizontal was 4, which left the outermost value
  // touching the card edge — Andy: "Values clip the screen edges ('280' on
  // the left, '-0.045' on the right)". The row carries a value, a unit and
  // a rank pill per side, so 4px of gutter is consumed by the pill's own
  // radius. 10 gives each side room without narrowing the label column
  // enough to re-wrap the stat names.
  statRow: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 8, paddingHorizontal: 10,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.borderSoft,
  },
  statLabel: {
    flex: 1.4, textAlign: 'center',
    color: C.text, opacity: 0.75, fontSize: 10, fontWeight: '700',
    letterSpacing: 0.04, textTransform: 'uppercase',
    paddingHorizontal: 6,
  },
  // minWidth 0 is required for the flex child to be allowed to shrink below
  // its content width; without it RN lets the value+unit+pill group push
  // past the row bounds instead of compressing, which is the other half of
  // the clipping above.
  statCell: {flex: 1.3, justifyContent: 'center', minWidth: 0},
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
// 2026-09-10 CRASH FIX: cohortRecords must be passed as a prop from the
// GameDetailV2 parent — SportSpecificSlot is a separate function component
// and can't access GameDetailV2's `cohortTagRecords` state via closure.
// Prior version referenced the state var directly here and every NFL/NCAAF
// game-detail open crashed with ReferenceError.
function SportSpecificSlot({ctx, gamesSport, game, cohortRecords}: any) {
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
    return <NFLSlot ctx={ctx} game={game} cohortRecords={cohortRecords} />;
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
    return <NHLSlot ctx={ctx} game={game} />;
  }
  return null;
}

// ─── NHL SLOT ────────────────────────────────────────────────────────────
// 2026-09-28. This slot was `return null` with the note "Goalie matchup +
// B2B chip ships once nhl_starters / nhl_goalies land. Reviewer safety."
//
// They landed. Measured across the 25 games on the board from 9/29:
//
//   home_goalie / away_goalie          25/25   *_goalie_confirmed  25/25
//   *_goalie_sv_pct / *_goalie_gsaa    23-24/25
//   home/away_pp_pct / pk_pct          25/25
//   *_xgf_per60 / *_xga_per60          23/25
//   *_5v5_cf / *_high_danger_for|against  23/25
//   elo_home / elo_away                25/25
//   *_rest_days / *_back_to_back       25/25
//
// So 68 of 121 columns were populated and the app rendered none of them —
// and NHL is the sport carrying the card from 9/29 (5 playable games that
// day, 3 on 9/30, 8 on 10/01, against NFL's 1 and MLB's 4).
//
// WHAT THIS DELIBERATELY DOES NOT ADD: a predicted-score card. The shared
// ScoreRange already covers NHL through addPred('v3', projected_total,
// projected_spread) plus MC from mc_probabilities, and (t±m)/2 reproduces
// the stored projected_home_goals / projected_away_goals exactly — 6.38 and
// 0.94 give 3.66 / 2.72, which is what those columns hold. A second card
// would be the same number computed in a second place.
//
// Every card here reads ctx only (no fetch) and returns null when its own
// inputs are absent, so a thin game silently shows fewer cards rather than
// a grid of dashes. Columns confirmed NEVER populated on NHL — every
// ATS/L10/OU tendency, h2h_*, *_travel_km, panel_pred_*, splits_summary,
// *_goalie_last5_sv_pct — are not referenced at all.
function NHLSlot({ctx, game}: any) {
  const homeTeam = ctx?.home_team || game?.home_team;
  const awayTeam = ctx?.away_team || game?.away_team;
  return (
    <>
      <NHLGoalieMatchupCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NHLSpecialTeamsCard  ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NHLShotQualityCard   ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NHLRestCard          ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
    </>
  );
}

// Shared comparison table for the NHL cards. `better` says which direction
// wins so the advantaged number can be highlighted: 'high' (PP%, CF%, xGF),
// 'low' (xGA, goals against) or null (no winner — don't colour either side).
// The loser keeps C.text rather than an undefined key: `{color: C.win}` with
// win undefined does not inherit, it clears the colour and RN paints black on
// a near-black ground, which is how 28 stats went invisible on 2026-09-25.
function NHLCompareRows({rows, awayTeam, homeTeam}: any) {
  return (
    <>
      <View style={{flexDirection: 'row', paddingBottom: 4, borderBottomWidth: 0.5,
                    borderBottomColor: C.border}}>
        <Text style={{flex: 1.5, color: C.textMuted, fontSize: 9, fontWeight: '800'}}>STAT</Text>
        <Text style={{flex: 1, color: C.away, fontSize: 9, fontWeight: '800',
                      textAlign: 'center'}}>{abbrev3(awayTeam)}</Text>
        <Text style={{flex: 1, color: C.home, fontSize: 9, fontWeight: '800',
                      textAlign: 'center'}}>{abbrev3(homeTeam)}</Text>
      </View>
      {rows.map((r: any, i: number) => {
        const aN = Number(r.awayRaw), hN = Number(r.homeRaw);
        let edge: 'away' | 'home' | null = null;
        if (r.better && isFinite(aN) && isFinite(hN) && aN !== hN) {
          const awayWins = r.better === 'high' ? aN > hN : aN < hN;
          edge = awayWins ? 'away' : 'home';
        }
        return (
          <View key={i} style={{flexDirection: 'row', paddingVertical: 5}}>
            <Text style={{flex: 1.5, color: C.textDim, fontSize: 12}}>{r.label}</Text>
            <Text style={{flex: 1, fontSize: 12, fontWeight: '700', textAlign: 'center',
                          color: edge === 'away' ? C.accent : C.text}}>{r.away}</Text>
            <Text style={{flex: 1, fontSize: 12, fontWeight: '700', textAlign: 'center',
                          color: edge === 'home' ? C.accent : C.text}}>{r.home}</Text>
          </View>
        );
      })}
    </>
  );
}

// Goalie matchup. The single largest predictive input in hockey, and it was
// entirely absent from the app.
//
// GSAA (Goals Saved Above Average) is the number that actually separates
// goalies and it is jargon, so the card spells out the sign rather than
// leaving the user to guess: on the 9/29 VGK game A. Hill sits at -14.17 on
// a .8705 save percentage against S. Knight at +10.9 and .902, which is a
// real edge the card previously showed nowhere.
function NHLGoalieMatchupCard({ctx, homeTeam, awayTeam}: any) {
  const isEnabled = useSectionEnabled('NHL', 'game_detail', 'goalie_matchup', true);
  const hG = ctx?.home_goalie, aG = ctx?.away_goalie;
  if (!isEnabled) return null;
  if (!hG && !aG) return null;
  const sv = (v: any) => v == null ? null : `.${String(Math.round(Number(v) * 1000)).padStart(3, '0')}`;
  const gsaa = (v: any) => v == null ? null
    : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(1)}`;
  const side = (team: string, name: any, confirmed: any, svp: any, g: any, tone: string) => (
    <View style={[styles.pitcherCard, {borderTopColor: tone, padding: 12, gap: 3, flex: 1}]}>
      <Text style={styles.pitcherName}>{name || 'TBD'}</Text>
      <Text style={{color: C.textMuted, fontSize: 9, fontWeight: '700',
                    letterSpacing: 0.4}}>{abbrev3(team)}{confirmed === true ? ' · CONFIRMED'
                    : confirmed === false ? ' · PROJECTED' : ''}</Text>
      {sv(svp) && <Text style={styles.pitcherStats}>SV%: <Text style={styles.pitcherStatBold}>{sv(svp)}</Text></Text>}
      {gsaa(g) != null && (
        <Text style={styles.pitcherStats}>GSAA: <Text style={[styles.pitcherStatBold,
          {color: Number(g) > 0 ? C.accent : Number(g) < 0 ? C.fade : C.text}]}>{gsaa(g)}</Text></Text>
      )}
    </View>
  );
  const anyGsaa = ctx?.home_goalie_gsaa != null || ctx?.away_goalie_gsaa != null;
  return (
    <Section title="Goalie Matchup" hint="save % + goals saved above average">
      <View style={{flexDirection: 'row', gap: 8}}>
        {side(awayTeam, aG, ctx?.away_goalie_confirmed, ctx?.away_goalie_sv_pct, ctx?.away_goalie_gsaa, C.away)}
        {side(homeTeam, hG, ctx?.home_goalie_confirmed, ctx?.home_goalie_sv_pct, ctx?.home_goalie_gsaa, C.home)}
      </View>
      {anyGsaa && (
        <Text style={{color: C.textDim, fontSize: 10, marginTop: 6}}>
          GSAA is goals prevented versus a league-average goalie on the same
          shots — above zero is better than average, below zero is worse.
        </Text>
      )}
    </Section>
  );
}

// Special teams, read as the CROSS matchup. A power play is only as good as
// the penalty kill it faces, so pairing each team's PP against the other's
// PK is the read; PP-vs-PP would compare two units that never meet.
function NHLSpecialTeamsCard({ctx, homeTeam, awayTeam}: any) {
  const isEnabled = useSectionEnabled('NHL', 'game_detail', 'special_teams', true);
  const hPP = ctx?.home_pp_pct, aPP = ctx?.away_pp_pct;
  const hPK = ctx?.home_pk_pct, aPK = ctx?.away_pk_pct;
  if (!isEnabled) return null;
  if (hPP == null && aPP == null && hPK == null && aPK == null) return null;
  const p = (v: any) => v == null ? '—' : `${Number(v).toFixed(1)}%`;
  // Net = how much better this PP is than the PK it meets. Positive favours
  // the skater advantage, negative favours the kill.
  const net = (pp: any, pk: any) => (pp == null || pk == null) ? null
    : Number(pp) - (100 - Number(pk));
  const nA = net(aPP, hPK), nH = net(hPP, aPK);
  const fmtNet = (v: any) => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`;
  return (
    <Section title="Special Teams" hint="each power play vs the kill it faces">
      <NHLCompareRows awayTeam={awayTeam} homeTeam={homeTeam} rows={[
        {label: 'Power play %', away: p(aPP), home: p(hPP),
         awayRaw: aPP, homeRaw: hPP, better: 'high'},
        {label: 'Penalty kill %', away: p(aPK), home: p(hPK),
         awayRaw: aPK, homeRaw: hPK, better: 'high'},
        {label: 'PP vs opp PK', away: fmtNet(nA), home: fmtNet(nH),
         awayRaw: nA, homeRaw: nH, better: 'high'},
      ]} />
      <Text style={{color: C.textDim, fontSize: 10, marginTop: 6}}>
        "PP vs opp PK" is this team's power-play rate minus the rate the
        opposing kill concedes — above zero means the advantage sits with the
        skater edge.
      </Text>
    </Section>
  );
}

// Shot quality + Elo: who generates and concedes the better chances.
// xG per 60 is rate-based; high-danger chances are season counts, so they
// are labelled as totals and paired with a differential rather than dressed
// up as a per-game rate they are not.
function NHLShotQualityCard({ctx, homeTeam, awayTeam}: any) {
  const isEnabled = useSectionEnabled('NHL', 'game_detail', 'shot_quality', true);
  const have = ['home_xgf_per60', 'away_xgf_per60', 'home_5v5_cf', 'away_5v5_cf',
                'elo_home', 'elo_away', 'home_high_danger_for', 'away_high_danger_for']
    .some(k => ctx?.[k] != null);
  if (!isEnabled) return null;
  if (!have) return null;
  const n2 = (v: any) => v == null ? '—' : Number(v).toFixed(2);
  const n0 = (v: any) => v == null ? '—' : Math.round(Number(v)).toString();
  const cf = (v: any) => v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`;
  const hdDiff = (f: any, a: any) => (f == null || a == null) ? null : Number(f) - Number(a);
  const dA = hdDiff(ctx?.away_high_danger_for, ctx?.away_high_danger_against);
  const dH = hdDiff(ctx?.home_high_danger_for, ctx?.home_high_danger_against);
  const rows: any[] = [];
  if (ctx?.elo_home != null || ctx?.elo_away != null) rows.push(
    {label: 'Elo rating', away: n0(ctx?.elo_away), home: n0(ctx?.elo_home),
     awayRaw: ctx?.elo_away, homeRaw: ctx?.elo_home, better: 'high'});
  if (ctx?.away_5v5_cf != null || ctx?.home_5v5_cf != null) rows.push(
    {label: '5v5 Corsi for %', away: cf(ctx?.away_5v5_cf), home: cf(ctx?.home_5v5_cf),
     awayRaw: ctx?.away_5v5_cf, homeRaw: ctx?.home_5v5_cf, better: 'high'});
  if (ctx?.away_xgf_per60 != null || ctx?.home_xgf_per60 != null) rows.push(
    {label: 'xG for /60', away: n2(ctx?.away_xgf_per60), home: n2(ctx?.home_xgf_per60),
     awayRaw: ctx?.away_xgf_per60, homeRaw: ctx?.home_xgf_per60, better: 'high'});
  if (ctx?.away_xga_per60 != null || ctx?.home_xga_per60 != null) rows.push(
    {label: 'xG against /60', away: n2(ctx?.away_xga_per60), home: n2(ctx?.home_xga_per60),
     awayRaw: ctx?.away_xga_per60, homeRaw: ctx?.home_xga_per60, better: 'low'});
  if (dA != null || dH != null) rows.push(
    {label: 'High-danger diff', away: dA == null ? '—' : `${dA > 0 ? '+' : ''}${n0(dA)}`,
     home: dH == null ? '—' : `${dH > 0 ? '+' : ''}${n0(dH)}`,
     awayRaw: dA, homeRaw: dH, better: 'high'});
  if (!rows.length) return null;
  return (
    <Section title="Shot Quality" hint="Elo · Corsi · expected goals">
      <NHLCompareRows awayTeam={awayTeam} homeTeam={homeTeam} rows={rows} />
      <Text style={{color: C.textDim, fontSize: 10, marginTop: 6}}>
        High-danger diff is season chances for minus against. Early in the
        season these team rates carry last season's data until this year's
        sample is large enough to stand on its own.
      </Text>
    </Section>
  );
}

// Rest, back-to-backs and road trips. NHL plays close to nightly, so the
// schedule is a live factor most nights rather than an occasional one — the
// same reason NBA has its own rest card.
function NHLRestCard({ctx, homeTeam, awayTeam}: any) {
  const isEnabled = useSectionEnabled('NHL', 'game_detail', 'rest_schedule', true);
  const hR = ctx?.home_rest_days, aR = ctx?.away_rest_days;
  const hB = ctx?.home_back_to_back, aB = ctx?.away_back_to_back;
  if (!isEnabled) return null;
  if (hR == null && aR == null && hB == null && aB == null) return null;
  const days = (v: any) => v == null ? '—' : `${Number(v)}d`;
  const b2b = (v: any) => v == null ? '—' : (v ? 'Yes' : 'No');
  const rows: any[] = [
    // 2026-09-28 · LABELLED FOR WHAT IT ACTUALLY COUNTS.
    // Andy: "FLO's rest days are wrong. It played 9/26, so it's on one day of
    // rest, not 3d." The number is right and the label was not:
    // nhl_game_context computes (game_date - last_game_date).days, which is
    // days ELAPSED, not days of rest. Florida played 9/26 and plays 9/29 —
    // three days elapsed, two days of rest (the 27th and the 28th).
    // Renaming is the safe fix: rest_days also drives back_to_back
    // (elapsed == 1) and the long-road-trip signal, so changing the stored
    // arithmetic would silently move those too.
    {label: 'Days since last game', away: days(aR), home: days(hR),
     awayRaw: aR, homeRaw: hR, better: 'high'},
    // Back-to-back is a yes/no, so there is no "better" number to colour —
    // a false/false row must not paint one side green for tying.
    {label: 'Back-to-back', away: b2b(aB), home: b2b(hB), better: null},
  ];
  // Road trips are NOT a compare row. away_consecutive_road_games is
  // populated on all 25 games but home_consecutive_road_games is populated on
  // NONE of them, so a two-column row would print a permanent "—" under the
  // home team and read as missing data rather than "the home team is home".
  // It also only carries information once a trip is actually long, so it
  // surfaces as a note at 2+ games and stays silent at 0 or 1.
  const aRoad = Number(ctx?.away_consecutive_road_games);
  const roadNote = isFinite(aRoad) && aRoad >= 2
    ? `${abbrev3(awayTeam)} is on game ${aRoad + 1} of a road trip.` : null;
  return (
    <Section title="Rest & Schedule" hint="days since last game · back-to-backs">
      <NHLCompareRows awayTeam={awayTeam} homeTeam={homeTeam} rows={rows} />
      {roadNote && (
        <Text style={{color: C.textDim, fontSize: 10, marginTop: 6}}>{roadNote}</Text>
      )}
    </Section>
  );
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
      {/* 2026-09-16: matched NFL slot's Situational panel — kickoff slot
          (Sat Big Noon / afternoon / primetime), conference-game chip,
          cold/wind chips outdoor when material. Cohort chips stay in
          SignalsRow one section up, no duplication. */}
      <NCAAFSituationalCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
    </>
  );
}

// ─── NCAAF SITUATIONAL (chips row) ──────────────────────────────────────
// Companion to NFLSituationalCard. NCAAF has no roof column (bulk of
// FBS games outdoors; the domes that exist are edge cases we don't
// flag), and rest days are usually uniform Sat→Sat so no rest chip
// unless we start ingesting midweek slates fully. Kickoff slot is
// the marquee CFB situational signal.
function NCAAFSituationalCard({ctx, homeTeam, awayTeam}: any) {
  const isEnabled = useSectionEnabled('NCAAF', 'game_detail', 'situational', true);
  if (!isEnabled) return null;
  const conf = ctx?.conference_game;
  const tags = ctx?.cohort_tags || [];
  const tagsHasConf = Array.isArray(tags)
    && tags.some((t: string) => /conf|div/i.test(String(t)));
  const showConfChip = conf && !tagsHasConf;

  // Kickoff slot — CFB fans think in slots. All ET.
  //   Fri night = Fri Night Lights
  //   Sat noon (12-1)   = Big Noon
  //   Sat 3:30-4:30     = Afternoon window
  //   Sat 7-8           = Primetime
  //   Sat 10:30+        = Late night (west coast)
  //   Thu/Fri primetime = Weeknight
  const kickoff = ctx?.kickoff_utc;
  let slotLabel: string | null = null;
  if (kickoff) {
    try {
      const dt = new Date(kickoff);
      const et = new Date(dt.getTime() - 4 * 3600 * 1000);
      const dow = et.getUTCDay(); // 0=Sun 5=Fri 6=Sat
      const hr  = et.getUTCHours();
      if (dow === 6) {
        if (hr >= 10 && hr < 13) slotLabel = 'Big Noon';
        else if (hr >= 15 && hr < 17) slotLabel = 'Afternoon';
        else if (hr >= 19 && hr < 21) slotLabel = 'Primetime';
        else if (hr >= 22 || hr < 3) slotLabel = 'Late Night';
      } else if (dow === 5) slotLabel = 'Friday Night';
      else if (dow === 4) slotLabel = 'Thursday Night';
    } catch {}
  }

  // NCAAF ctx has no roof column — assume outdoor for cold/wind gates.
  const cold  = ctx?.temp != null && Number(ctx.temp) < 30;
  const wind  = ctx?.wind != null && Number(ctx.wind) > 15;

  const hasAny = slotLabel || showConfChip || cold || wind;
  if (!hasAny) return null;
  return (
    <Section title="Situational">
      <View style={{flexDirection: 'row', flexWrap: 'wrap', gap: 6}}>
        {slotLabel && <SitChip label={`Slot · ${slotLabel}`} kind="info" />}
        {showConfChip && <SitChip label="Conference game" />}
        {cold && <SitChip label={`Cold · ${Math.round(Number(ctx.temp))}°F`} kind="warn" />}
        {wind && <SitChip label={`Wind · ${Math.round(Number(ctx.wind))} mph`} kind="warn" />}
      </View>
    </Section>
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
  // 2026-09-08: server-controlled visibility (see NFLTeamMatchupCard note).
  // NCAAF slot is separately togglable from NFL — different sport row in
  // config_ui_sections. Both default true.
  //
  // 2026-09-10 CRASH FIX (same bug as NFLTeamMatchupCard): if (!isEnabled)
  // return null was between the useStates and the useEffect. When
  // config_ui_sections loaded with enabled=false, early return fired,
  // skipping the useEffect → "Rendered fewer hooks than expected" crash on
  // every NCAAF game detail open. Fix: move useEffect above the early
  // return, gate the fetch body on isEnabled inside.
  const isEnabled = useSectionEnabled('NCAAF', 'game_detail', 'team_matchup', true);
  const [stats, setStats] = React.useState<{home?: any; away?: any}>({});
  const [seasonUsed, setSeasonUsed] = React.useState<number | null>(null);
  React.useEffect(() => {
    if (!isEnabled) return;
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
          // 2026-09-13 FIX: prior substring-both-directions match caused
          // "Georgia State" query to grab "Georgia" (Bulldogs) row because
          // "georgia state".includes("georgia") === true. Same class breaks
          // Georgia Tech, Georgia Southern, Miami/Miami (Ohio), etc.
          // New rule: exact match FIRST, then substring only when the
          // CFBD name has >=2 words (prevents single-word school matches
          // from grabbing state/tech/southern variants). Andy screenshot
          // 9/12 caught Georgia State card showing Georgia Tech's stats.
          const _pickTeam = (norm: string) => {
            // 1. exact
            const exact = data.find((r: any) => _norm(r.team) === norm);
            if (exact) return exact;
            // 2. substring, but reject single-word CFBD names (too greedy)
            return data.find((r: any) => {
              const t = _norm(r.team);
              const tWords = t.split(' ').filter(Boolean).length;
              const nWords = norm.split(' ').filter(Boolean).length;
              // CFBD short (e.g. "Virginia") vs display long (e.g.
              // "Virginia Cavaliers") — OK when CFBD has >=2 words, OR
              // display is exactly CFBD + " <one-word-mascot>".
              if (tWords >= 2 && (norm.includes(t) || t.includes(norm))) return true;
              if (tWords === 1 && nWords === 2 && norm.startsWith(t + ' ')) {
                // Only accept if the trailing word looks like a mascot,
                // not a location differentiator like "State"/"Tech"/"Southern".
                const trail = norm.slice(t.length + 1);
                const _LOC = new Set(['state','tech','southern','western','eastern','northern','a&m']);
                return !_LOC.has(trail);
              }
              return false;
            });
          };
          const homeRow = _pickTeam(hNorm);
          const awayRow = _pickTeam(aNorm);
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
  }, [homeTeam, awayTeam, ctx?.season, isEnabled]);
  if (!isEnabled) return null;

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
                          letterSpacing: 0.6, marginBottom: 2, textAlign: 'center'}}>ADVANCED METRICS</Text>
            {/* 2026-09-02: plain-language explainer for casual bettors —
                metrics themselves stay abbreviated for handicapper-density,
                but a one-line legend explains direction of "better." */}
            <Text style={{color: C.textDim, fontSize: 9, marginBottom: 6, textAlign: 'center', fontStyle: 'italic'}}>
              Higher = better on offense · Lower = better on defense
            </Text>
            <StatRow label="POWER RATING" a={spA} b={spH} aAdv={spAdv.a} bAdv={spAdv.b}
                     fmt={(v: any) => v == null ? '—' : Number(v).toFixed(1)} />
            <StatRow label="OFFENSE / PLAY" a={offA} b={offH} aAdv={offAdv.a} bAdv={offAdv.b}
                     fmt={(v: any) => v == null ? '—' : Number(v).toFixed(2)} />
            <StatRow label="DEFENSE / PLAY" a={defA} b={defH} aAdv={defAdv.a} bAdv={defAdv.b}
                     fmt={(v: any) => v == null ? '—' : Number(v).toFixed(2)} />
            <StatRow label="SUCCESS %"  a={succOffA} b={succOffH}
                     aAdv={succOffA != null && succOffH != null && succOffA > succOffH}
                     bAdv={succOffA != null && succOffH != null && succOffH > succOffA}
                     fmt={(v: any) => v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`} />
            <StatRow label="BIG PLAYS"  a={explA} b={explH}
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
  // Same section_key as NCAAFRostersCard — either variant hides together.
  // Update: consider distinct section_keys ('rosters_rich' vs 'rosters')
  // if you want to toggle them independently. For now, treated as one
  // logical section with two render variants.
  const isEnabled = useSectionEnabled('NCAAF', 'game_detail', 'rosters_continuity', true);
  // 2026-09-26: "RETURN PROD — UTEP -1%. Returning production can't be
  // negative." Correct — it is the share of last season's production
  // coming back, so the domain is 0..1. Measured across the NCAAF board:
  // 3 of 159 values are slightly negative (-0.06, -0.01), which is a
  // small computation artifact around zero rather than a real quantity.
  // Clamped to the real domain at the display so the card never states
  // something impossible; the upstream fix belongs with whoever
  // computes the column.
  const _clampRP = (v: any) => {
    const n = Number(v);
    if (v == null || !isFinite(n)) return v;
    return Math.min(1, Math.max(0, n));
  };
  const rpH = _clampRP(ctx?.home_returning_production);
  const rpA = _clampRP(ctx?.away_returning_production);
  if (!isEnabled) return null;
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

  // 2026-09-26: `.split(' ').pop()` takes the LAST word, so "Oregon
  // State" became "State" and "Boston College" would become "College".
  // Andy: "'State OL outweighs opposing DL by 50 lb' — 'Oregon State'
  // truncated to 'State', so the string builder is taking the last token
  // of the team name." abbrev3() is the shortener the rest of this file
  // already uses and it knows real team names.
  const notes: string[] = [];
  if (olGapH != null && Number(olGapH) >= 15)
    notes.push(`${abbrev3(homeTeam)} OL outweighs opposing DL by ${Math.round(Number(olGapH))} lb — ground-game leverage.`);
  if (olGapA != null && Number(olGapA) >= 15)
    notes.push(`${abbrev3(awayTeam)} OL outweighs opposing DL by ${Math.round(Number(olGapA))} lb.`);
  // 2026-10-09 WEEK GATE. This note carried its own expiry in its text —
  // "(Weeks 1-3 significant)" — and then rendered in Week 7 regardless,
  // because the only condition was the magnitude of classEdge. We were
  // showing a claim that admits it no longer applies. `week` is on the
  // context row, so gate on it and drop the parenthetical: inside the window
  // the caveat is unnecessary, outside it the note should not exist.
  const _wk = Number(ctx?.week ?? ctx?.season_week);
  const _classWindow = Number.isFinite(_wk) && _wk >= 1 && _wk <= 3;
  if (_classWindow && classEdge != null && Math.abs(Number(classEdge)) >= 0.3)
    notes.push(`${abbrev3(Number(classEdge) > 0 ? homeTeam : awayTeam)} carries a class-year experience edge.`);

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
// 2026-09-14 v1.0.1 #6: wrapped in config_ui_sections toggle so a
// weather-source outage can be muted by SQL (both sports at once).
function SportWeatherCard({ctx, sport = 'NCAAF'}: any) {
  const isEnabled = useSectionEnabled(sport, 'game_detail', 'weather', true);
  const temp = ctx?.temp;
  const wind = ctx?.wind;
  const dome = ctx?.dome;
  const src  = ctx?.weather_source;
  if (!isEnabled) return null;
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
  const isEnabled = useSectionEnabled('NCAAF', 'game_detail', 'efficiency_ratings', true);
  const spHome = ctx?.home_sp_overall;
  const spAway = ctx?.away_sp_overall;
  if (!isEnabled) return null;
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
  const isEnabled = useSectionEnabled('NCAAF', 'game_detail', 'rosters_continuity', true);
  const rpHome = ctx?.home_returning_production;
  const rpAway = ctx?.away_returning_production;
  if (!isEnabled) return null;
  const olGapH = ctx?.ol_dl_weight_gap_home;
  const olGapA = ctx?.ol_dl_weight_gap_away;
  const classEdge = ctx?.class_year_edge_home;
  const homeClassYr = ctx?.home_avg_class_year;
  const awayClassYr = ctx?.away_avg_class_year;
  const homeOl = ctx?.home_ol_avg_wt;
  const awayOl = ctx?.away_ol_avg_wt;
  // 2026-10-09: the class-year note is only claimed significant in Weeks 1-3
  // and had no week gate, so it rendered all season. Gate once here and reuse
  // below; `classWindow` folds the week check into the same flag the note
  // already tested so the two render sites cannot drift apart again.
  const _wk = Number(ctx?.week ?? ctx?.season_week);
  const classWindow = Number.isFinite(_wk) && _wk >= 1 && _wk <= 3;
  const showClassNote = classWindow && classEdge != null
    && Math.abs(Number(classEdge)) >= 0.3;
  const hasAny = rpHome != null || rpAway != null || olGapH != null || olGapA != null ||
                 classEdge != null || homeOl != null || awayOl != null;
  if (!hasAny) return null;
  // Same last-word truncation as the notes above — abbrev3 instead.
  const awayShort = abbrev3(awayTeam);
  const homeShort = abbrev3(homeTeam);
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
          showClassNote) && (
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
            {showClassNote && (
              <Text style={[styles.pitcherStats, {color: C.accent}]}>
                {Number(classEdge) > 0 ? homeShort : awayShort} carries a class-year experience edge
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
  const isEnabled = useSectionEnabled(sport || 'NFL', 'game_detail', 'trends_tendencies', true);
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

  if (!isEnabled) return null;
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
function NFLSlot({ctx, game, cohortRecords}: any) {
  const homeTeam = ctx?.home_team || game?.home_team;
  const awayTeam = ctx?.away_team || game?.away_team;
  return (
    <>
      {/* 2026-09-16: NFLJerryLockNote moved OUT of the sport slot and up
          next to JerryReadSection (see main flow ~line 525). Andy audit:
          the Thursday-lock explainer belongs adjacent to the read it
          explains, not floating in the middle of the situational block
          two scrolls below. The <JerryLockNote/> render at the top of
          the main flow now owns this. */}
      <SportWeatherCard ctx={ctx} sport="NFL" />
      <NFLQBMatchupCard  ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NFLTeamMatchupCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      <NFLInjuriesCard   ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} />
      {/* TeamTendenciesCard removed 2026-09-01 — see NCAAF slot note. */}
      <NFLSituationalCard ctx={ctx} homeTeam={homeTeam} awayTeam={awayTeam} cohortRecords={cohortRecords} />
    </>
  );
}

// 2026-09-02: Thu-lock explanation banner. Reads ui_notes for
// 'nfl_jerry_lock_note' / 'ncaaf_jerry_lock_note' (sport-aware) —
// backend-editable copy per project_backend_notes_901.
// Silent-hide if fetch fails / note missing.
// 2026-09-16: renamed NFLJerryLockNote → JerryLockNote and moved
// to render adjacent to JerryReadSection (main flow) instead of
// mid-page inside NFLSlot. Andy audit: the note belongs next to
// the read it explains.
function JerryLockNote({sport}: {sport: string}) {
  const [note, setNote] = React.useState<string | null>(null);
  const noteKey = sport === 'NCAAF' ? 'ncaaf_jerry_lock_note' : 'nfl_jerry_lock_note';
  React.useEffect(() => {
    (async () => {
      try {
        const client = sb();
        if (!client) return;
        const {data} = await client.from('ui_notes')
          .select('note_text').eq('note_key', noteKey).eq('enabled', true).limit(1);
        if (Array.isArray(data) && data[0]?.note_text) setNote(data[0].note_text);
      } catch { /* silent hide if table missing or fetch fails */ }
    })();
  }, [noteKey]);
  if (!note) return null;
  return (
    <View style={{
      backgroundColor: C.accent + '12', borderRadius: 8, padding: 10, marginBottom: 12,
      borderLeftWidth: 3, borderLeftColor: C.accent,
    }}>
      <Text style={{color: C.text, fontSize: 12, lineHeight: 17}}>{note}</Text>
    </View>
  );
}

// ─── NFL QB MATCHUP ─────────────────────────────────────────────────────
// Buffed from the prior "Starting QBs" name-only card. Now joins Sleeper
// projections (nfl_player_projections, 802 rows live per 2026-08-25) so
// each starter shows projected fantasy pts + season Y/A when available.
function NFLQBMatchupCard({ctx, homeTeam, awayTeam}: any) {
  const isEnabled = useSectionEnabled('NFL', 'game_detail', 'qb_matchup', true);
  const [starters, setStarters] = useState<{home?: any; away?: any}>({});
  const [projections, setProjections] = useState<{home?: any; away?: any}>({});
  if (!isEnabled) return null;
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
          // 2026-09-19: was proj_pass_yards — no such column; the real one
          // is proj_pass_yds. One bad name 400s the whole select, so the QB
          // Matchup card lost its projections entirely (not just pass yards).
          .select('player_name,team,proj_fantasy_pts,proj_pass_yds,proj_pass_tds')
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
      {proj?.proj_pass_yds != null && (
        <Text style={styles.pitcherStats}>
          Pass Y: <Text style={styles.pitcherStatBold}>{Math.round(Number(proj.proj_pass_yds))}</Text>
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
  // 2026-09-08: server-controlled visibility via config_ui_sections.
  // Team Matchup is redundant with Team Stats on NFL Game Detail per user
  // feedback 9/8. Flipping enabled=false on the DB row hides this section
  // without an app rebuild. Default = true (backwards-compatible on first
  // v1.0.1 deploy; DB update is the way to actually hide it).
  //
  // 2026-09-10 CRASH FIX — this component was crashing every NFL game
  // detail with "Rendered fewer hooks than expected." Root cause:
  // `if (!isEnabled) return null;` was placed BETWEEN the useState and
  // the useEffect. First render defaulted enabled=true (3 hooks); then
  // when config_ui_sections loaded with enabled=false (we flipped it via
  // DB toggle on 9/10 to hide the redundant section) the early return
  // fired, skipping the useEffect and yielding only 2 hooks. Rules of
  // Hooks: ALL hooks must run before ANY conditional return.
  // Fix: moved useEffect above the early return, added a hasSummary guard
  // inside the effect body so it stays a no-op when not needed.
  // 2026-09-15: DEFAULT FLIPPED true→false. Was gated via config_ui_sections
  // DB row (see original 9/8 note) but no one ever flipped the row, so the
  // section kept rendering every deploy. Andy has flagged it as redundant
  // with Team Stats multiple times. Hide by default; DB toggle can still
  // set enabled=true to re-surface it if needed.
  const isEnabled = useSectionEnabled('NFL', 'game_detail', 'team_matchup', false);
  const [fallback, setFallback] = useState<{home?: any; away?: any} | null>(null);
  const hasSummary = ctx?.home_team_stats_summary || ctx?.away_team_stats_summary;
  React.useEffect(() => {
    // Skip the fetch when the section is hidden OR when the server already
    // populated a summary — same behavior as before, just gated inside the
    // effect body so the hook itself always runs.
    if (!isEnabled) return;
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
  }, [homeTeam, awayTeam, ctx?.season, hasSummary, isEnabled]);
  if (!isEnabled) return null;

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
  const isEnabled = useSectionEnabled('NFL', 'game_detail', 'injuries', true);
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
  if (!isEnabled) return null;
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

// ─── NFL SITUATIONAL (chips row — divisional, rest gap, primetime, cohort tags) ────
// Weather chips REMOVED here; the shared SportWeatherCard renders them
// as a proper section higher up.
// 2026-09-16 expansion (Andy audit "what other badges go here?"): added
// PRIMETIME slot chip (TNF/SNF/MNF), SHORT WEEK chip (either team ≤4d rest),
// COLD-WEATHER chip (outdoor + temp < 30°F when temp populated), and
// HIGH-WIND chip (>15 mph outdoor). Cohort chips still live in SignalsRow
// one section up — no duplication. Section now consistently earns space
// on ~60% of NFL cards vs prior ~15%.
function NFLSituationalCard({ctx, homeTeam, awayTeam, cohortRecords}: any) {
  const isEnabled = useSectionEnabled('NFL', 'game_detail', 'situational', true);
  const tags = ctx?.cohort_tags || [];
  const rest = {home: ctx?.home_rest, away: ctx?.away_rest};
  const roof = ctx?.roof;
  if (!isEnabled) return null;
  const div = ctx?.div_game;
  const restGap = (rest.home != null && rest.away != null && Math.abs(rest.home - rest.away) >= 3);
  const tagsHasDiv = Array.isArray(tags)
    && tags.some((t: string) => String(t).toLowerCase().includes('div'));
  const showDivChip = div && !tagsHasDiv;

  // 2026-09-16: Primetime slot from kickoff_utc. NFL primetime windows:
  //   TNF Thu 8:15 ET (00:15 UTC Fri), SNF Sun 8:20 ET (00:20 UTC Mon),
  //   MNF Mon 8:15 ET (00:15 UTC Tue). Detect by weekday+hour in ET.
  const kickoff = ctx?.kickoff_utc;
  let primetimeLabel: string | null = null;
  if (kickoff) {
    try {
      const dt = new Date(kickoff);
      // ET = UTC - 4 (EDT) / -5 (EST). Sep is EDT.
      const et = new Date(dt.getTime() - 4 * 3600 * 1000);
      const dow = et.getUTCDay(); // 0=Sun 4=Thu 1=Mon
      const hr  = et.getUTCHours();
      if (dow === 4 && hr >= 20) primetimeLabel = 'TNF';
      else if (dow === 0 && hr >= 19) primetimeLabel = 'SNF';
      else if (dow === 1 && hr >= 19) primetimeLabel = 'MNF';
    } catch {}
  }

  // 2026-09-16: Short-week chip when either team is on ≤4d rest — the
  // NFL short-week penalty is a documented signal (SHORT_WEEK cohort).
  const shortRestSide = (rest.home != null && rest.home <= 4) ? homeTeam
                       : (rest.away != null && rest.away <= 4) ? awayTeam
                       : null;
  const shortRestDays = shortRestSide === homeTeam ? rest.home : rest.away;

  // 2026-09-16: Cold + wind chips ONLY when roof is outdoor AND the value
  // is materially bad. Below 30°F (elite cold), >15 mph wind (real air-ball
  // territory). Weather section shows the raw numbers already; these are
  // the "this actually matters" flags.
  const outdoor = roof && String(roof).toLowerCase() === 'outdoor';
  const cold  = outdoor && ctx?.temp != null && Number(ctx.temp) < 30;
  const wind  = outdoor && ctx?.wind != null && Number(ctx.wind) > 15;

  const hasAny = showDivChip || roof || restGap || primetimeLabel || shortRestSide || cold || wind;
  if (!hasAny) return null;
  return (
    <Section title="Situational">
      <View style={{flexDirection: 'row', flexWrap: 'wrap', gap: 6}}>
        {primetimeLabel && <SitChip label={`Primetime · ${primetimeLabel}`} kind="info" />}
        {showDivChip && <SitChip label="Divisional" record={cohortRecords?.['nfl_div_home_cover|ats']} />}
        {shortRestSide && (
          <SitChip label={`Short week · ${abbrev3(shortRestSide)} ${shortRestDays}d`} kind="warn" />
        )}
        {restGap && (
          <SitChip label={`Rest edge · ${abbrev3(rest.home > rest.away ? homeTeam : awayTeam)} +${Math.abs(rest.home - rest.away)}d`} kind="info" />
        )}
        {roof && <SitChip label={`Roof: ${roof.charAt(0).toUpperCase() + roof.slice(1)}`} />}
        {cold && <SitChip label={`Cold · ${Math.round(Number(ctx.temp))}°F`} kind="warn" />}
        {wind && <SitChip label={`Wind · ${Math.round(Number(ctx.wind))} mph`} kind="warn" />}
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

function SitChip({label, kind = 'neutral', record}: {
  label: string;
  kind?: 'ok'|'warn'|'info'|'neutral';
  record?: {wins: number; losses: number; pushes: number; hit_rate: number; sample_n: number; market?: string};
}) {
  // 2026-09-10: DEFENSIVE ROLLBACK — the inline nested-Text record render
  // was correlating with a "Rendered fewer hooks than expected" crash on
  // NFL game detail. Reverting to plain-label-only until root cause is
  // isolated. The records still ride along as a prop so re-enabling is
  // a one-line change, but we no longer render them client-side.
  // Auto-color by hit rate stays — reads record but doesn't render it.
  let effectiveKind = kind;
  if (record && record.hit_rate != null) {
    const hr = Number(record.hit_rate);
    if (isFinite(hr)) {
      if (hr >= 55) effectiveKind = 'ok';
      else if (hr <= 45) effectiveKind = 'warn';
    }
  }
  return (
    <View style={[styles.sitChip, chipStyleFor(effectiveKind)]}>
      <Text style={[styles.sitChipText, {color: chipTextColorFor(effectiveKind)}]}>
        {label}
      </Text>
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
  const isEnabled = useSectionEnabled('NBA', 'game_detail', 'team_snapshot', true);
  const h = {net: ctx?.home_net_rating, pace: ctx?.home_pace, off: ctx?.home_off_rating, def: ctx?.home_def_rating, elo: ctx?.elo_home};
  const a = {net: ctx?.away_net_rating, pace: ctx?.away_pace, off: ctx?.away_off_rating, def: ctx?.away_def_rating, elo: ctx?.elo_away};
  if (!isEnabled) return null;
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
  const isEnabled = useSectionEnabled('NBA', 'game_detail', 'rest_b2b', true);
  if (!isEnabled) return null;
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
  const isEnabled = useSectionEnabled('NBA', 'game_detail', 'injuries', true);
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
  if (!isEnabled) return null;
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
  const isEnabled = useSectionEnabled('NBA', 'game_detail', 'four_factors', true);
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
  if (!isEnabled) return null;
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
  const isEnabled = useSectionEnabled('NCAAB', 'game_detail', 'efficiency_panel', true);
  if (!isEnabled) return null;
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
  const isEnabled = useSectionEnabled('NCAAB', 'game_detail', 'pace_tempo', true);
  if (!isEnabled) return null;
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
  const isEnabled = useSectionEnabled('NCAAB', 'game_detail', 'four_factors_ordered', true);
  if (!isEnabled) return null;
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
  const isEnabled = useSectionEnabled('NCAAB', 'game_detail', 'form_rest', true);
  if (!isEnabled) return null;
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
// 2026-09-10: swap raw snake_case names → prettyCohortTag() lookup + fall back
// to Title Case; swap "HOME"/"AWAY" text → team abbrev (SEA / NE), which is
// what the user actually recognizes. Signal name + team abbrev pair reads as
// "Home-Field Edge → SEA" instead of "hfa → HOME". Records column will be
// wired in when cohort_tag_records rollup ships (project_cohort_signal_ux_909).
function CohortsPanel({ctx, cohortRecords}: any) {
  const cb = safeJSON(ctx?.signal_confluence_breakdown) || {};
  const items = Object.entries(cb).filter(([_, v]) => v === 'home' || v === 'away');
  if (items.length === 0) return <Text style={styles.emptyMuted}>No cohort signals fired.</Text>;
  const homeAbbr = abbrev3(ctx?.home_team || '');
  const awayAbbr = abbrev3(ctx?.away_team || '');
  // 2026-09-10: DEFENSIVE ROLLBACK — record render suspected in the
  // "Rendered fewer hooks" NFL game-detail crash. Reverted to labels +
  // sides only. cohortRecords still received but not rendered until we
  // isolate the root cause and re-enable safely.
  return (
    <View style={styles.cohortsGrid}>
      {items.map(([name, side]: any, i) => (
        <View key={i} style={[
          styles.cohort,
          {borderLeftColor: side === 'home' ? C.home : C.away},
        ]}>
          <Text style={styles.cohortName}>{prettyCohortTag(String(name))}</Text>
          <Text style={[styles.cohortSide, {color: side === 'home' ? C.home : C.away}]}>
            {side === 'home' ? homeAbbr : awayAbbr}
          </Text>
        </View>
      ))}
    </View>
  );
}

// ─── GAME PROPS PANEL ────────────────────────────────────────────────────
// 2026-10-09 rework. Three defects fixed and one capability added:
//
//  1. THE PRICE WAS NEVER SHOWN. book_over_odds / book_under_odds were in the
//     view all along and simply not selected. A prop row without its price
//     cannot be evaluated — the user could not see a -250 offer inside a
//     -300..+150 band, nor the documented Batter Hits O0.5 juice trap.
//  2. "Projected" COULD NEVER RENDER on the self-fetch path. The row read
//     `projected_value ?? projected` and NEITHER column exists in
//     v_mlb_props_publishable, so the field was dead whenever the modal
//     fetched its own props. Replaced with season hit% / L10, which do exist.
//  3. SEVEN PROPS VANISHED SILENTLY. The fetch took 15 and the panel sliced
//     to 8 with nothing saying more existed — the same truncation class as
//     project_postgrest_truncation_audit_912. Now the cap is explicit and the
//     remainder is stated.
//
//  + INFO-ONLY ROWS. NHL props arrive tagged `_infoOnly` because they are
//    surfaced as data, not plays (-6.57% ROI on n=4,247, every OVER family
//    -11..-21%). Those rows get NO tier pill and NO conviction, because a
//    tier badge is a claim we cannot support here.
function GamePropsPanel({props: propsList}: {props: any[]}) {
  const CAP = 10;
  if (!propsList || propsList.length === 0) {
    return <Text style={styles.emptyMuted}>No qualifying props for this game.</Text>;
  }
  const infoOnly = propsList.some((p: any) => p?._infoOnly);
  const total = Number(propsList[0]?._totalBeforeCap) || propsList.length;
  // 2026-10-10 B69 · GROUP BY PROP FAMILY. A flat list of ten rows mixing
  // hits, strikeouts and bases forces the reader to scan for the family they
  // care about, and the backlog note is explicit that filtering UI on top of
  // an ungrouped list would sort the wrong field. Grouping is the cheap half
  // of "better surfacing" and needs no new pipeline.
  //
  // Order is by the group's best conviction, not alphabetical, so the
  // strongest family stays at the top where the old flat ordering put it —
  // grouping must not bury the best play.
  const shown = propsList.slice(0, CAP);
  const famOf = (p: any) => String(p?.prop_type || 'other')
    .replace(/_(over|under)$/i, '')
    .replace(/_/g, ' ')
    .trim() || 'other';
  const groups: {fam: string; rows: any[]; best: number}[] = [];
  for (const p of shown) {
    const fam = famOf(p);
    let g = groups.find((x) => x.fam === fam);
    if (!g) {
      g = {fam, rows: [], best: -1};
      groups.push(g);
    }
    g.rows.push(p);
    const cv = Number(p?.conviction);
    if (Number.isFinite(cv) && cv > g.best) g.best = cv;
  }
  groups.sort((a, b) => b.best - a.best);
  return (
    <View style={{gap: 4}}>
      {infoOnly && (
        <Text style={[styles.propDetail, {fontSize: 10, marginBottom: 6, lineHeight: 15}]}>
          Model coverage for this game — every skater and goalie line our model
          priced, shown for reference. These are not published picks and carry
          no tier.
        </Text>
      )}
      {groups.map((grp) => (
      <View key={grp.fam} style={{gap: 4}}>
      {groups.length > 1 && (
        <Text style={styles.propFamilyHdr}>{grp.fam}</Text>
      )}
      {grp.rows.map((p: any, i: number) => {
        const isOver = String(p.direction || '').toLowerCase() === 'over';
        const dir = isOver ? '↑' : '↓';
        // The price of the side actually being referenced — over props take
        // the over price. Reading one column for both directions is the
        // "check WHICH SIDE a column indexes" trap.
        const odds = isOver ? p.book_over_odds : p.book_under_odds;
        const oddsNum = Number(odds);
        const hasOdds = odds != null && Number.isFinite(oddsNum);
        const seasonPct = p.player_season_hit_pct ?? null;
        // 2026-10-10 B69 · THE PRICE NEEDS A VERDICT, NOT JUST A VALUE.
        // Showing -250 shipped on 10-09; a number alone still asks the user
        // to remember the house rules. Two documented traps, both of which
        // cost real money before they were written down:
        //   * the publishable band is -300..+150, so anything outside it
        //     should never have reached a card
        //   * Batter Hits OVER 0.5 worse than -200 is its own trap and is
        //     not publishable even at PRIME (feedback_batter_hits_juice_trap)
        // A flag is strictly better than a colour alone: colour is invisible
        // to a colour-blind reader and carries no meaning on its own.
        const fam = famOf(p);
        const isHitsOver05 = isOver && /hits/.test(fam)
          && Number(p.prop_line) === 0.5;
        const outOfBand = hasOdds && (oddsNum < -300 || oddsNum > 150);
        const hitsTrap = hasOdds && isHitsOver05 && oddsNum < -200;
        const heavyJuice = hasOdds && !outOfBand && !hitsTrap
          && oddsNum <= -200;
        const priceWarn = outOfBand ? 'outside -300..+150'
          : hitsTrap ? 'Hits O0.5 juice trap'
          : heavyJuice ? 'heavy juice' : null;
        // C.loss does NOT exist in this file's palette — it has `fade` for
        // red and `warn` for amber. Using C.loss here would resolve to
        // undefined and ship BLACK text, which is the documented
        // undefined-palette-key trap (feedback_undefined_palette_key_renders
        // _black). Keys verified against the C block at the top of this file.
        const priceColor = (outOfBand || hitsTrap) ? C.fade
          : heavyJuice ? C.warn : C.text;
        return (
          <View key={i}>
          <View style={styles.propRow}>
            {!p._infoOnly && (
              <View style={[styles.propTier, tierPillStyle(p.tier)]}>
                <Text style={[styles.propTierText, {color: tierPillTextColor(p.tier)}]}>{p.tier}</Text>
              </View>
            )}
            <View style={{flex: 1}}>
              <Text style={styles.propPlayer} numberOfLines={1}>{p.player_name || '?'}</Text>
              <Text style={styles.propDetail}>
                Line <Text style={styles.propBold}>{p.prop_line}</Text> {String(p.prop_type || '')}
                {seasonPct != null && <> · season <Text style={styles.propBold}>{f(seasonPct, 0)}%</Text></>}
              </Text>
            </View>
            <View style={{alignItems: 'flex-end'}}>
              <Text style={[styles.propDetail, {color: C.text, fontWeight: '700'}]}>{dir} {p.direction}</Text>
              {/* Price sits where the eye lands last, beside the side it
                  belongs to. An unpriced row says so rather than showing a
                  blank that reads as -110. */}
              <Text style={[styles.propDetail,
                            {fontSize: 11, fontWeight: '700',
                             color: priceColor}]}>
                {hasOdds ? (oddsNum > 0 ? `+${oddsNum}` : `${oddsNum}`) : 'no price'}
              </Text>
              {priceWarn && (
                <Text style={[styles.propDetail,
                              {fontSize: 9, color: priceColor}]}>
                  ⚠ {priceWarn}
                </Text>
              )}
              {!p._infoOnly && p.conviction != null && (
                <Text style={[styles.propDetail, {fontSize: 9}]}>conv {p.conviction}</Text>
              )}
            </View>
          </View>
          {/* 2026-10-10 B69 · JERRY'S READ WAS SELECTED AND NEVER RENDERED.
              jerry_short_read / jerry_verdict / jerry_conviction were added
              to the select on 10-09 and then appeared nowhere else in this
              file — the query was widened and the fields were never wired to
              a row. That is the substance of "a better way of surfacing in
              game prop look": the content already exists per prop.

              BUT IT CANNOT BE RENDERED RAW. jerry_short_read is a ~23-line
              TERMINAL block: a header restating the pick and price, a 60-char
              box-drawing rule, SIGNAL COVERAGE, an averages line, a ten-game
              RECENT FORM list, then PLAYBOOK CONFIRMS bullets. Dumping it
              into a mobile row would print box characters and newlines.
              So the two lines that actually earn their space here are
              extracted:
                 ' L5 avg 3.4 · L10 avg 4.0 · Season avg 4.63 · Implied 64%'
                 ' → 8/10 games OVER 2.5 (2 UNDER)'
              The header is dropped because the row already shows player,
              line, direction and price.

              Defensive by construction: if the format changes, the matches
              simply fail and nothing renders, rather than leaking raw
              terminal output into the UI. */}
          {(() => {
            const raw = p.jerry_short_read;
            if (!raw) return null;
            const lines = String(raw).split('\n')
              .map((s: string) => s.trim())
              // drop box-drawing rules and empties
              .filter((s: string) => s && !/^[─—=_-]{6,}$/.test(s));
            const avg = lines.find((s: string) => /^L5 avg/i.test(s));
            const hit = lines.find((s: string) => /^→/.test(s));
            const keep = [hit, avg].filter(Boolean) as string[];
            if (!keep.length) return null;
            // The NFL rows arrive with doubled spaces around their separators
            // (' · ' vs ' ·  '), so collapse runs of whitespace rather than
            // letting one sport look ragged next to the other.
            const body = keep.join(' · ')
              .replace(/^→\s*/, '').replace(/\s{2,}/g, ' ').trim();
            // A verdict is not always positive. jerry_verdict carries PASS and
            // FADE as well as PRIME/STRONG, and 4 of today's MLB rows include
            // a PASS. Painting PASS in the accent green would read as a
            // recommendation — the opposite of what it says. Colour by the
            // verdict's meaning, not by the fact that a verdict exists.
            const vd = String(p.jerry_verdict || '').toUpperCase();
            const vdColor = /PASS|FADE|AVOID/.test(vd) ? C.warn
              : /PRIME|STRONG/.test(vd) ? C.accent : C.textMuted;
            return (
              <Text style={[styles.propDetail,
                            {fontSize: 10, lineHeight: 14, marginTop: -1,
                             marginBottom: 3, paddingLeft: 52}]}>
                {!p._infoOnly && !!vd && (
                  <Text style={{color: vdColor, fontWeight: '700'}}>
                    {vd}
                    {p.jerry_conviction != null ? ` ${p.jerry_conviction}` : ''}
                    {' · '}
                  </Text>
                )}
                {body}
              </Text>
            );
          })()}
          </View>
        );
      })}
      </View>
      ))}
      {total > shown.length && (
        <Text style={[styles.propDetail, {fontSize: 10, marginTop: 4}]}>
          Showing {shown.length} of {total}.
        </Text>
      )}
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

  // 2026-09-26 · THE LINE ON A TILE MUST BE THE LINE WE WOULD GRADE.
  //
  // Andy, on Oregon State @ UTEP: "the read and the odds tiles are bound
  // to the opening line, not the current one ... the total one matters
  // most, because the OVER/UNDER tiles are what a user taps to log a
  // pick." The MARKET header read Total 56.5 while these tiles read
  // O 55.5 / U 55.5, and line movement confirmed 55.5 -> 56.5.
  //
  // Cause: the tiles preferred the per-book snapshot (`outcome.point`)
  // over the consensus close, and that snapshot can be older than
  // ctx.close_total. Two surfaces on one screen then disagree.
  //
  // The tie-break is not "which is prettier", it is which number the
  // receipt will be graded against — the consensus close. A user who
  // taps Under 55.5 and gets graded at 56.5 has been shown the wrong
  // bet. So the consensus wins the LINE, while the book still supplies
  // the PRICE, which is genuinely book-specific and not something we
  // grade against.
  const _pick = (consensus: any, bookPoint: any) =>
    (consensus != null ? consensus : bookPoint);

  // 2026-09-26 · WHICH TEAM LAYS THE POINTS COMES FROM THE MONEYLINE.
  //
  // Andy, on Carolina @ Cleveland: "three sources have Carolina as the
  // favorite; the spread tiles have Cleveland ... these are the tiles a
  // user taps to log a pick, so the logged bet would be the wrong side."
  // Correct, and this one is the most dangerous bug on the card.
  //
  // Cause: close_spread's SIGN CONVENTION IS NOT THE SAME ACROSS SPORTS,
  // which is documented (project_close_spread_sign_bug_914) and which I
  // walked straight into when I made these tiles prefer the consensus:
  //
  //     NFL    MIN @ SF   close_spread +3.5  -> SF (HOME) favoured
  //     NCAAF  ORST @ UTEP close_spread +11.5 -> UTEP (HOME) is the DOG
  //
  // Opposite meanings for the same sign. So ANY fix that reads the sign
  // is correct in one sport and inverted in the other, which is exactly
  // how CAR @ CLE (close_spread -2.5, home ML +120, away ML -142) came
  // out as "CLE -2.5" when Carolina is the favourite.
  //
  // The moneyline has no such ambiguity in any sport: the negative price
  // is the favourite, always. So take the MAGNITUDE from the spread and
  // the DIRECTION from the moneyline. That is convention-proof, and it
  // also self-checks — the tiles can no longer disagree with the ML
  // tiles sitting beside them on the same row.
  const _mag = closeSpread != null ? Math.abs(Number(closeSpread)) : null;
  const _hML = Number(homeML), _aML = Number(awayML);
  const _homeIsFav = (isFinite(_hML) && isFinite(_aML)) ? _hML < _aML : null;
  const _homeConsensus = (_mag != null && _homeIsFav != null)
    ? (_homeIsFav ? -_mag : _mag) : null;
  const spreadHomeLine = _pick(_homeConsensus, homeSpreadOutcome?.point);
  const spreadHomeOdds = homeSpreadOutcome?.price;
  const spreadAwayLine = _pick(_homeConsensus != null ? -_homeConsensus : null,
                               awaySpreadOutcome?.point);
  const spreadAwayOdds = awaySpreadOutcome?.price;
  const totalLine = _pick(closeTotal, overOutcome?.point);
  const overOdds = overOutcome?.price;
  const underOdds = underOutcome?.price;
  // Surfaced so the user is told when their book is off the consensus
  // rather than silently shown one number here and another in the header.
  const _bookTotal = overOutcome?.point;
  const totalStale = (_bookTotal != null && closeTotal != null
                      && Number(_bookTotal) !== Number(closeTotal));
  const _bookSpread = homeSpreadOutcome?.point;
  const spreadStale = (_bookSpread != null && _homeConsensus != null
                       && Number(_bookSpread) !== Number(_homeConsensus));
  const finalHomeML = homeMLOutcome?.price ?? homeML;
  const finalAwayML = awayMLOutcome?.price ?? awayML;

  // 2026-09-26 · DO NOT PRE-LOAD A BET WE JUST TOLD THEM NOT TO MAKE.
  //
  // Andy: "A 'not a recommended play' still has Log Pick and +Parlay
  // fully enabled. The card says 'Not a recommended play — thin signal
  // support or unplayable price' and then offers Selected: KC ML @ -625
  // with both action buttons live."
  //
  // On that card the tier was COVERAGE at -625 — a price we would not
  // publish anywhere — and the panel had it pre-selected and one tap
  // from a logged bet. Disabling the buttons outright is the wrong
  // answer: a user is entitled to back whatever they like, and greying
  // out the whole panel would also block the OTHER five tiles, which
  // are perfectly fine bets.
  //
  // So the fix is narrower: a low-conviction play is not auto-selected.
  // The tile is still there, still tappable, still loggable — we simply
  // stop doing it FOR them, which is what turned a disclaimer into an
  // endorsement.
  const _lowConv = ['COVERAGE', 'PASS', 'SKIP']
    .includes(String(primaryPlay?.tier || '').toUpperCase());

  // Default-select the primary_play if it maps to one of our tiles
  const primaryDefault: any = (() => {
    if (_lowConv) return null;
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
  // Does the currently selected tile happen to BE the low-conviction play?
  const _selectedIsPrimary = (() => {
    if (!selected || !primaryPlay?.label) return false;
    const a = String(selected.pickLabel || '').toLowerCase();
    const b = String(primaryPlay.label || '').toLowerCase();
    return !!a && (a === b || b.includes(a) || a.includes(b));
  })();

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

      {/* 2026-09-26: say so when the book's own number has drifted off the
          consensus, instead of quietly showing one line here and another
          in the MARKET header. The tiles now show the consensus (what a
          logged pick is graded against); this tells the user what their
          book had when we last saw it. */}
      {(totalStale || spreadStale) && (
        <Text style={[styles.hrbSelectionHint, {color: C.textDim}]}>
          {`lines shown are the current consensus · this book last posted `
            + [spreadStale ? `spread ${_bookSpread > 0 ? '+' : ''}${_bookSpread}` : null,
               totalStale ? `total ${_bookTotal}` : null]
              .filter(Boolean).join(' · ')}
        </Text>
      )}

      {/* Selection status + Action buttons */}
      <Text style={styles.hrbSelectionHint}>
        {selected
          ? <>Selected: <Text style={{color: C.accent, fontWeight: '700'}}>{selected.pickLabel}</Text> {selected.odds != null ? `@ ${fmtOdds(selected.odds)}` : ''}</>
          : 'Tap a tile above to select a pick'}
      </Text>
      {/* If they pick the low-conviction play anyway, restate it here
          rather than letting the disclaimer sit forgotten at the top of
          a long card. Informed, not blocked. */}
      {_lowConv && selected && _selectedIsPrimary && (
        <Text style={{color: C.warn, fontSize: 10, textAlign: 'center', marginTop: 2}}>
          this is the low-conviction read — not a play we recommend
        </Text>
      )}

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
  // 2026-09-16: sort by market completeness so books with all three
  // (spread + total + h2h) top the list, partials sink to the bottom.
  // Early-week NFL/NCAAF slates commonly show 21 books but only 3-5 have
  // priced spread + ML yet — sorting keeps the actionable rows on top so
  // the "empty spreads" impression from a partial-slate screenshot goes
  // away as soon as more books post. Hard Rock Bet still pins first among
  // its completeness bucket.
  const _mktCount = (bm: any): number => {
    const keys = new Set((bm.markets || []).map((m: any) => m.key));
    return (keys.has('spreads') ? 1 : 0) + (keys.has('totals') ? 1 : 0) + (keys.has('h2h') ? 1 : 0);
  };
  const sorted = [...bookmakers].sort((a: any, b: any) => {
    const aHRB = /hardrock|hard rock/i.test(a.key || a.title || '');
    const bHRB = /hardrock|hard rock/i.test(b.key || b.title || '');
    if (aHRB && !bHRB) return -1;
    if (bHRB && !aHRB) return 1;
    const mc = _mktCount(b) - _mktCount(a);
    if (mc !== 0) return mc;
    return (a.title || '').localeCompare(b.title || '');
  });

  const addLeg = (kind: string, label: string, odds: any, line: any, bookTitle: string) => {
    onAddParlayLeg?.({kind, label, odds, line, matchup: `${awayTeam} @ ${homeTeam}`, book: bookTitle});
  };

  // 2026-09-07 BEST-PRICE HIGHLIGHTING (queue item #3). Scan every book's
  // odds per market and find the max-value price. Flag winners with a
  // gold ★ chip in the cell so users can shop lines at a glance without
  // squinting at the full table. American-odds "best" = highest number
  // for underdog prices (+ larger is better) OR closest-to-zero for
  // favorite prices (-105 beats -110). Universal formula: convert to
  // decimal, take max. Applied to away ML, home ML, home spread price,
  // over total price. Line values (spread/total point) already visible
  // per row — we shop by juice, not line.
  const _toDec = (american: any): number | null => {
    if (american == null) return null;
    const n = Number(american);
    if (!isFinite(n) || n === 0) return null;
    return n > 0 ? 1 + n / 100 : 1 + 100 / Math.abs(n);
  };
  // ══ 2026-09-26 · A TOTAL IS SHOPPED BY THE NUMBER FIRST, THEN THE JUICE ══
  // The comment above says "we shop by juice, not line", which is true for
  // moneylines and defensible for spreads, and wrong for totals. Comparing
  // only price starred DraftKings O53.5 while seven books offered O52.5 —
  // a better number for an over bettor at almost identical juice — and four
  // offered O54, better for an under bettor. 53.5 is the middle: the one
  // number no side should want. Third build Andy has caught this on.
  //
  // The star on the TOTAL column now means "best OVER": lowest line wins,
  // and price breaks ties. Half a point of total is worth far more than the
  // 2 cents of juice that was deciding it before.
  const bestLines: Record<string, {book: string; line: number; price: any}> = {};
  const markLineAsc = (bookTitle: string, marketKey: string, line: any, price: any) => {
    const ln = Number(line);
    if (!isFinite(ln)) return;
    const cur = bestLines[marketKey];
    if (!cur || ln < cur.line
        || (ln === cur.line && (_toDec(price) ?? 0) > (_toDec(cur.price) ?? 0))) {
      bestLines[marketKey] = {book: bookTitle, line: ln, price};
    }
  };
  const bestPrices: Record<string, {book: string; price: number}> = {};
  const markKey = (bookTitle: string, marketKey: string, price: any) => {
    const dec = _toDec(price);
    if (dec == null) return;
    const cur = bestPrices[marketKey];
    if (!cur || dec > _toDec(cur.price)!) {
      bestPrices[marketKey] = {book: bookTitle, price};
    }
  };
  for (const bm of sorted) {
    const spreadMkt = bm.markets?.find((m: any) => m.key === 'spreads');
    const totalMkt = bm.markets?.find((m: any) => m.key === 'totals');
    const h2hMkt = bm.markets?.find((m: any) => m.key === 'h2h');
    const homeSpread = spreadMkt?.outcomes?.find((o: any) => o.name === homeTeam);
    const overTot = totalMkt?.outcomes?.find((o: any) => (o.name || '').toLowerCase() === 'over');
    const awayML = h2hMkt?.outcomes?.find((o: any) => o.name === awayTeam);
    const homeML = h2hMkt?.outcomes?.find((o: any) => o.name === homeTeam);
    markKey(bm.title || bm.key, 'homeSpread', homeSpread?.price);
    markLineAsc(bm.title || bm.key, 'overTotal', overTot?.point, overTot?.price);
    markKey(bm.title || bm.key, 'awayML', awayML?.price);
    markKey(bm.title || bm.key, 'homeML', homeML?.price);
  }
  const isBestFor = (bookTitle: string, marketKey: string): boolean =>
    marketKey === 'overTotal'
      ? bestLines[marketKey]?.book === bookTitle
      : bestPrices[marketKey]?.book === bookTitle;

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{paddingRight: 12}}>
      <View style={{minWidth: 320}}>
        {/* Header row */}
        <View style={styles.bookTableHeader}>
          <Text style={[styles.bookTh, {flex: 1.6}]}>Book</Text>
          <Text style={[styles.bookTh, {flex: 1.6, textAlign: 'right'}]}>Spread</Text>
          {/* 2026-09-26: the star here means best OVER (lowest number), which
              is side-specific in a way the spread and ML stars are not. Say so
              in the header rather than leaving a bare star to be misread. */}
          <Text style={[styles.bookTh, {flex: 1.2, textAlign: 'right'}]}>Total (O)</Text>
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
              {/* 2026-09-16 text-darkness fix: prior code set color: undefined
                  in the override object, which on some RN versions collapses
                  the base bookTd color to platform default (Android reads it
                  as near-black on our dark surface). Andy audit: "text is too
                  dark". Conditional-spread only when best-price, so the base
                  C.text always wins for non-best cells. */}
              <TouchableOpacity
                style={{flex: 1.6}}
                onPress={() => homeSpread && addLeg('spread', `${abbrev3(homeTeam)} ${homeSpread.point > 0 ? '+' : ''}${homeSpread.point}`, homeSpread.price, homeSpread.point, bm.title)}
                activeOpacity={0.6}
              >
                <Text style={[styles.bookTd, {textAlign: 'right'}, isBestFor(bm.title || bm.key, 'homeSpread') && {color: C.accent, fontWeight: '800'}]}>
                  {isBestFor(bm.title || bm.key, 'homeSpread') ? '★ ' : ''}
                  {homeSpread ? `${homeSpread.point > 0 ? '+' : ''}${homeSpread.point}` : '—'}
                  {homeSpread?.price ? ` (${fmtOdds(homeSpread.price)})` : ''}
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={{flex: 1.2}}
                onPress={() => overTot && addLeg('total', `O ${overTot.point}`, overTot.price, overTot.point, bm.title)}
                activeOpacity={0.6}
              >
                <Text style={[styles.bookTd, {textAlign: 'right'}, isBestFor(bm.title || bm.key, 'overTotal') && {color: C.accent, fontWeight: '800'}]}>
                  {isBestFor(bm.title || bm.key, 'overTotal') ? '★ ' : ''}
                  {overTot ? `O${overTot.point}` : '—'}
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={{flex: 1}}
                onPress={() => awayML && addLeg('ml', `${abbrev3(awayTeam)} ML`, awayML.price, null, bm.title)}
                activeOpacity={0.6}
              >
                <Text style={[styles.bookTd, {textAlign: 'right'}, isBestFor(bm.title || bm.key, 'awayML') && {color: C.accent, fontWeight: '800'}]}>
                  {isBestFor(bm.title || bm.key, 'awayML') ? '★ ' : ''}{fmtOdds(awayML?.price)}
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={{flex: 1}}
                onPress={() => homeML && addLeg('ml', `${abbrev3(homeTeam)} ML`, homeML.price, null, bm.title)}
                activeOpacity={0.6}
              >
                <Text style={[styles.bookTd, {textAlign: 'right'}, isBestFor(bm.title || bm.key, 'homeML') && {color: C.accent, fontWeight: '800'}]}>
                  {isBestFor(bm.title || bm.key, 'homeML') ? '★ ' : ''}{fmtOdds(homeML?.price)}
                </Text>
              </TouchableOpacity>
            </View>
          );
        })}
      </View>
    </ScrollView>
  );
}

// ─── NUMBERS PANEL ──────────────────────────────────────────────────────
// 2026-09-16: rewrote to be sport-aware. Prior version read MLB-only field
// names (panel_implied_margin, jerry_pred_spread, model_pred_spread,
// mc_expected_margin) which do NOT exist on nfl_game_context /
// ncaaf_game_context — Andy screenshot showed only v3 row populated,
// every other row all "—". NFL panel/v4 data actually IS in the DB under
// different column names; NCAAF has SP+ instead of Panel. MC + Jerry
// numeric rows hidden for football (Jerry emits pick+read, not numbers;
// MC is MLB-only).
function NumbersPanel({ctx, awayTeam, homeTeam, sport}: any) {
  const mc = safeJSON(ctx?.mc_probabilities) || {};
  const isFootball = sport === 'NFL' || sport === 'NCAAF';

  // Helper: build margin+total+home/away pts from a home/away points pair.
  // NFL/NCAAF backend emits home_pts/away_pts pairs (not implied margin).
  // margin sign convention here: positive = home advantage (matches
  // ctx.projected_spread which is home-favored positive for NFL/NCAAF v3).
  const _row = (label: string, homePts: any, awayPts: any, totalOverride?: any, spreadOverride?: any) => {
    const hp = homePts != null ? Number(homePts) : null;
    const ap = awayPts != null ? Number(awayPts) : null;
    const margin = spreadOverride != null ? Number(spreadOverride)
                 : (hp != null && ap != null ? hp - ap : null);
    const total  = totalOverride != null ? Number(totalOverride)
                 : (hp != null && ap != null ? hp + ap : null);
    return [label, margin, total, ap, hp];
  };

  let rows: any[] = [];
  if (isFootball) {
    // v3 legacy formula-based projection
    rows.push(['v3', ctx?.projected_spread, ctx?.projected_total, null, null]);
    // v4 XGBoost margin from home/away points
    rows.push(_row('v4', ctx?.model_pred_home_points, ctx?.model_pred_away_points));
    if (sport === 'NFL') {
      // Panel is NFL-specific (fantasy-projection aggregate → team totals)
      rows.push(_row('Panel', ctx?.panel_pred_home_pts, ctx?.panel_pred_away_pts, ctx?.panel_pred_total));
    } else if (sport === 'NCAAF') {
      // SP+ (Bill Connelly ratings-derived spread + total)
      rows.push(_row('SP+', ctx?.sp_plus_pred_home_pts, ctx?.sp_plus_pred_away_pts,
                     ctx?.sp_plus_pred_total, ctx?.sp_plus_pred_spread));
    }
    // LR shadow — probabilities, expressed as home-win % (no margin/total)
    const pp = ctx?.primary_play || {};
    const lrMl = pp._lr_ml_shadow || {};
    const lrP = lrMl.p_home_win != null ? Number(lrMl.p_home_win) : null;
    if (lrP != null && isFinite(lrP)) {
      rows.push([`LR (${Math.round(lrP * 100)}% ${sport === 'NFL' ? 'HOME' : (lrP >= 0.5 ? 'HOME' : 'AWAY')})`,
                 null, null, null, null]);
    }
  } else {
    // MLB (unchanged from prior version)
    rows = [
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
  }

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

      {/* MC Probabilities block: MLB-only. NFL/NCAAF have no MC sim populated. */}
      {!isFootball && (
        <>
          <Text style={styles.numbersHeading}>MC Probabilities (10k sims)</Text>
          <View style={styles.numbersMCGrid}>
            <MCTile label="Home win prob" value={mc.mc_home_win_prob != null ? `${(mc.mc_home_win_prob * 100).toFixed(1)}%` : '—'} />
            <MCTile label="Away win prob" value={mc.mc_away_win_prob != null ? `${(mc.mc_away_win_prob * 100).toFixed(1)}%` : '—'} />
            <MCTile label="Over prob" value={mc.mc_p_over != null ? `${(mc.mc_p_over * 100).toFixed(1)}%` : '—'} />
            <MCTile label="Under prob" value={mc.mc_p_under != null ? `${(mc.mc_p_under * 100).toFixed(1)}%` : '—'} />
            <MCTile label="Mean total" value={mc.mc_mean_total != null ? f(mc.mc_mean_total, 2) : '—'} />
            <MCTile label="Std total" value={mc.mc_std_total != null ? f(mc.mc_std_total, 2) : '—'} />
            {sport === 'MLB' && <MCTile label="NRFI prob" value={mc.mc_p_nrfi != null ? `${(mc.mc_p_nrfi * 100).toFixed(1)}%` : '—'} />}
            {sport === 'MLB' && <MCTile label="YRFI prob" value={mc.mc_p_yrfi != null ? `${(mc.mc_p_yrfi * 100).toFixed(1)}%` : '—'} />}
          </View>
        </>
      )}

      {/* Football win-probability tiles from LR shadow. Uses primary_play
          shadows because NFL/NCAAF don't run MC. Silent-hide if the LR
          shadow isn't wired for this game (missing on FCS / Week 1). */}
      {isFootball && (() => {
        const pp = ctx?.primary_play || {};
        const lrMl = pp._lr_ml_shadow || {};
        const lrTot = pp._lr_total_shadow || {};
        const lrP  = lrMl.p_home_win != null ? Number(lrMl.p_home_win) : null;
        const lrOv = lrTot.p_over != null ? Number(lrTot.p_over) : null;
        if (lrP == null && lrOv == null) return null;
        return (
          <>
            <Text style={styles.numbersHeading}>LR Shadow Probabilities</Text>
            <View style={styles.numbersMCGrid}>
              {lrP != null && (
                <>
                  <MCTile label="Home win prob" value={`${(lrP * 100).toFixed(1)}%`} />
                  <MCTile label="Away win prob" value={`${((1 - lrP) * 100).toFixed(1)}%`} />
                </>
              )}
              {lrOv != null && (
                <>
                  <MCTile label="Over prob" value={`${(lrOv * 100).toFixed(1)}%`} />
                  <MCTile label="Under prob" value={`${((1 - lrOv) * 100).toFixed(1)}%`} />
                </>
              )}
            </View>
          </>
        );
      })()}

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

function SplitsSummaryPanel({summary, sport}: any) {
  const s = summary || {};
  const srcs: string[] = Array.isArray(s.sources_present) ? s.sources_present : [];
  const triple: string[] = Array.isArray(s.triple_confirmed) ? s.triple_confirmed : [];
  const MARKETS = ['ml', 'rl', 'total'];
  // 2026-09-02: rl label was hardcoded "Run/Puck Line" — showed on NCAAF /
  // NFL / NBA / NCAAB where it should say "Spread". Uses shared rlLabel()
  // helper (line 940) with sport-aware map (MLB → Run Line, NHL → Puck
  // Line, else → Spread).
  const marketLabel: Record<string, string> = {
    ml: 'Moneyline',
    rl: rlLabel(sport),
    total: 'Total',
  };
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
              // 2026-09-07: same fallback as MoneyFlow — cz/fr sources emit
              // handle_pct_avg, oc emits money_pct_avg. Prefer money, fall back.
              const money = agg?.money_pct_avg ?? agg?.handle_pct_avg;
              const bets = agg?.bets_pct_avg;
              const nSrc = agg?.sources_agree ?? 0;
              // 2026-09-01: adaptive confirmation chip. Was TRIPLE-only which
              // lied for NCAAF/NCAAB/NHL where max sources ≤ 2. Now:
              //   nSrc >= 3 → TRIPLE (cyan/sharp) — real moat signal
              //   nSrc == 2 → DOUBLE (cyan-dim) — decent signal
              //   nSrc == 1 → UNCONFIRMED chip (grey) — one source only, not
              //               a real agreement signal (2026-09-02 fix: prior
              //               version rendered these rows identically to
              //               multi-source rows and reads as "signal" when
              //               it's really just one book's read).
              const isTriple = nSrc >= 3;
              const isDouble = nSrc === 2;
              const isSingle = nSrc === 1;
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
                  {isSingle && (
                    <Text style={{color: C.textMuted, fontSize: 9, fontWeight: '700', fontStyle: 'italic'}}>
                      1 SRC
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
  // 2026-09-26: the hint was clipping mid-word ("…color marks a clear
  // edge; th…") on its third straight build. Shortening the copy was
  // never the fix — the row is flexDirection:'row' and the hint had no
  // flexShrink, so ANY hint wider than the leftover space gets cut at the
  // container edge instead of wrapping. flexShrink lets it wrap, and
  // flexShrink:0 on the title stops the label collapsing instead.
  // Structural, so every current and future hint is covered.
  sectionTitleRow: {flexDirection: 'row', justifyContent: 'space-between',
                    alignItems: 'flex-start', marginBottom: 10, gap: 10},
  sectionTitle: {fontSize: 10, fontWeight: '700', color: C.textMuted,
                 textTransform: 'uppercase', letterSpacing: 1.2, flexShrink: 0},
  sectionHint: {fontSize: 10, color: C.textDim, fontStyle: 'italic',
                flexShrink: 1, textAlign: 'right'},
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
  // Two-word tile values ("CHI · STRONG") at a size that fits the ~38px a
  // flex:1 lens gets in a seven-tile row, with centred wrapping so the tier
  // drops to a second line instead of breaking inside the word.
  lensMarginSmall: {fontSize: 9, lineHeight: 11, textAlign: 'center'},
  lensTotal: {fontSize: 9, fontVariant: ['tabular-nums']},

  // Handicappers
  // 2026-09-27: wrapping moved to handiChipWrap so the trailing count can
  // no longer wrap with the chips. The row itself stays on one line and
  // grows in height as the chip container wraps inside it.
  handiRow: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 4, paddingVertical: 6,
  },
  handiChipWrap: {
    flex: 1, minWidth: 0,
    flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 4,
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
  // 2026-10-10 B69 · prop-family group header. Uppercase + letter-spacing so
  // it reads as a label rather than another data row, and textDim rather than
  // textMuted so it recedes behind the props themselves.
  propFamilyHdr: {
    fontSize: 9, fontWeight: '700', color: C.textDim, letterSpacing: 0.8,
    textTransform: 'uppercase', marginTop: 6, marginBottom: 1,
  },

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
  // 2026-09-15: bumped text sizes + column widths — Andy audit: "text hard
  // to see and looks misaligned". Spread column widened to fit "-1.5 (-105)"
  // without wrap; base bookTd size 11 → 12 for readability; header size 9 →
  // 10; row padding 6 → 8 for breathing room.
  bookTableHeader: {
    flexDirection: 'row', paddingVertical: 8, paddingHorizontal: 6,
    borderBottomWidth: 1, borderBottomColor: C.border, gap: 8, marginBottom: 4,
  },
  bookTh: {fontSize: 10, color: C.textMuted, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5},
  bookTableRow: {
    flexDirection: 'row', paddingVertical: 8, paddingHorizontal: 6, gap: 8,
    borderBottomWidth: 1, borderBottomColor: C.border,
  },
  bookTd: {fontSize: 12, color: C.text, fontVariant: ['tabular-nums']},

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
