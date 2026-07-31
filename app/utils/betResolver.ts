/**
 * Sport-universal bet resolver (2026-07-31 · Tabletop C).
 *
 * Grades a user's logged bet against server-side game_results (per-sport
 * table dispatch) + prop results (from mlb_pipeline_props etc.). Sport
 * dispatch makes it plug-and-play — adding NBA/NFL/NHL requires only:
 *   1. That sport's *_game_results table populated by its pipeline
 *   2. (For props) That sport's *_pipeline_props table (MLB first, others
 *      as pipelines land)
 *
 * Handles: ML, Spread, Total, Prop, Parlay (multi-leg AND).
 *
 * Falls back to Odds API scores when server results not yet posted
 * (early morning after games) — preserves existing behavior.
 *
 * Client-side only for MVP. Server-side auto-resolver (cron-driven,
 * cross-device sync) is future work when user accounts land.
 */
import {SupabaseClient} from '@supabase/supabase-js';

type Sport = 'MLB'|'NBA'|'NFL'|'NCAAF'|'NCAAB'|'NHL'|'UFC';
type BetType = 'ML'|'Spread'|'Total'|'Prop'|'Parlay';
type Result = 'Win'|'Loss'|'Push'|'Void'|'Pending';

export type Bet = {
  id: string | number;
  sport: Sport | string;
  type: BetType | string;
  matchup: string;                  // "Team A @ Team B" or "Player Name" for props
  pick: string;                     // "Yankees ML" / "Over 8.5" / "Judge Over 1.5 HRs" / etc.
  odds?: string;
  result: Result | string;
  legs?: Array<{sport?: string; type: string; matchup: string; pick: string; result?: string}>; // Parlay only
  game_date?: string;               // ISO date if known — narrows server lookup
};

export type ResolveOutcome = {
  result: Result;
  detail: string;                   // human-readable "Final: 5-3 · Under 8.5"
  source: 'server_game'|'server_prop'|'odds_api'|'parlay_composite'|'skip';
};

// ─── Per-sport table registry ───────────────────────────────────────────
const RESULTS_TABLE: Record<string, string | null> = {
  MLB: 'mlb_game_results',
  NBA: 'nba_game_results',
  NFL: 'nfl_game_results',
  NCAAF: 'ncaaf_game_results',
  NCAAB: 'ncaab_game_results',
  NHL: null,                        // no *_game_results table yet
  UFC: 'ufc_fight_results',         // different shape — handled specially
};

const PROPS_TABLE: Record<string, string | null> = {
  MLB: 'mlb_pipeline_props',        // graded by pipeline
  // Add per-sport prop tables as they ship
  NBA: null, NFL: null, NCAAF: null, NCAAB: null, NHL: null, UFC: null,
};

// ─── Helpers ────────────────────────────────────────────────────────────
const norm = (s: string) => (s || '').toLowerCase().trim();
const _lastWord = (s: string) => norm(s).split(/\s+/).slice(-1)[0] || '';

/** Team-name fuzzy match — matches on last word (e.g. "Yankees" vs "New York Yankees"). */
function _teamHit(pickTeam: string, gameTeam: string): boolean {
  const p = norm(pickTeam); const g = norm(gameTeam);
  if (!p || !g) return false;
  if (p === g) return true;
  if (g.includes(p) || p.includes(g)) return true;
  return _lastWord(p) === _lastWord(g);
}

function _parseMatchup(m: string): {away: string; home: string} | null {
  const parts = (m || '').split(/@|vs\.?/i).map(s => s.trim()).filter(Boolean);
  if (parts.length < 2) return null;
  return {away: parts[0], home: parts[1]};
}

/** Extract line from pick text: "Over 8.5" → 8.5; "Yankees -5.5" → -5.5. */
function _parseLine(pick: string): number | null {
  const m = pick.match(/([+-]?\d+(?:\.\d+)?)/);
  return m ? parseFloat(m[1]) : null;
}

/** Detect if pick text points at away or home based on team name. */
function _pickSide(pick: string, away: string, home: string): 'HOME'|'AWAY'|null {
  const p = norm(pick.replace(/[+-]?\d+(\.\d+)?\s*$/, '').replace(/\bml\b|\bmoneyline\b|\brl\b/i, '').trim());
  if (!p) return null;
  if (_teamHit(p, home)) return 'HOME';
  if (_teamHit(p, away)) return 'AWAY';
  return null;
}

// ─── Server-side game result lookup ─────────────────────────────────────
async function _fetchServerGameResult(
  supabase: SupabaseClient, sport: string, away: string, home: string, gameDate?: string,
): Promise<any | null> {
  const table = RESULTS_TABLE[sport];
  if (!table) return null;
  let q = supabase.from(table)
    .select('home_team,away_team,home_score,away_score,total_result,run_line_result,spread_result')
    .not('home_score', 'is', null);
  if (gameDate) q = q.eq('game_date', gameDate);
  const {data, error} = await q.limit(50).order('game_date', {ascending: false});
  if (error || !data) return null;
  return data.find((g: any) =>
    _teamHit(away, g.away_team) && _teamHit(home, g.home_team)
  ) || null;
}

// ─── Per-market resolvers (sport-agnostic) ──────────────────────────────
function _resolveML(pick: string, game: any, matchup: string): ResolveOutcome | null {
  const parsed = _parseMatchup(matchup);
  if (!parsed) return null;
  const side = _pickSide(pick, parsed.away, parsed.home);
  if (!side) return null;
  const hs = Number(game.home_score); const as = Number(game.away_score);
  if (!isFinite(hs) || !isFinite(as)) return null;
  const won = (side === 'HOME' && hs > as) || (side === 'AWAY' && as > hs);
  const push = hs === as;
  return {
    result: push ? 'Push' : (won ? 'Win' : 'Loss'),
    detail: `Final: ${parsed.away} ${as} – ${hs} ${parsed.home}`,
    source: 'server_game',
  };
}

function _resolveSpread(pick: string, game: any, matchup: string): ResolveOutcome | null {
  const parsed = _parseMatchup(matchup);
  if (!parsed) return null;
  const line = _parseLine(pick);
  if (line === null) return null;
  const side = _pickSide(pick, parsed.away, parsed.home);
  if (!side) return null;
  const hs = Number(game.home_score); const as = Number(game.away_score);
  if (!isFinite(hs) || !isFinite(as)) return null;
  const adj = side === 'HOME' ? hs + line : as + line;
  const opp = side === 'HOME' ? as : hs;
  const r: Result = adj > opp ? 'Win' : (adj < opp ? 'Loss' : 'Push');
  return {result: r, detail: `Final: ${as}-${hs} · adj ${adj} vs ${opp}`, source: 'server_game'};
}

function _resolveTotal(pick: string, game: any): ResolveOutcome | null {
  const line = _parseLine(pick);
  if (line === null) return null;
  const hs = Number(game.home_score); const as = Number(game.away_score);
  if (!isFinite(hs) || !isFinite(as)) return null;
  const combined = hs + as;
  const isOver = /over/i.test(pick);
  if (combined === line) return {result: 'Push', detail: `Total ${combined} = line ${line}`, source: 'server_game'};
  const won = isOver ? combined > line : combined < line;
  return {
    result: won ? 'Win' : 'Loss',
    detail: `Total ${combined} vs ${isOver ? 'Over' : 'Under'} ${line}`,
    source: 'server_game',
  };
}

// ─── Prop resolver (server-graded) ──────────────────────────────────────
async function _resolveProp(
  supabase: SupabaseClient, sport: string, bet: Bet,
): Promise<ResolveOutcome | null> {
  const table = PROPS_TABLE[sport];
  if (!table) return null;   // sport doesn't have prop pipeline yet
  // Best-effort match: strip trailing "Over X.X" from pick to get player name
  const cleanPick = bet.pick.replace(/\b(over|under)\b.*/i, '').trim();
  const {data} = await supabase.from(table)
    .select('player_name,prop_type,direction,prop_line,result')
    .not('result', 'in', '(Pending,null)')
    .order('game_date', {ascending: false})
    .limit(100);
  if (!data || !data.length) return null;
  // Fuzzy match on player_name last word
  const hit = data.find((p: any) => {
    if (!p.player_name || !p.result) return false;
    return _teamHit(cleanPick, p.player_name);
  });
  if (!hit) return null;
  const norm_r = String(hit.result).toLowerCase();
  const r: Result = norm_r === 'win' ? 'Win' : (norm_r === 'loss' ? 'Loss' : (norm_r === 'push' ? 'Push' : 'Void'));
  return {result: r, detail: `${hit.player_name} ${hit.prop_type} ${hit.direction} ${hit.prop_line}`, source: 'server_prop'};
}

// ─── Parlay resolver (all legs must Win) ────────────────────────────────
async function _resolveParlay(
  supabase: SupabaseClient, bet: Bet,
  gameResolvers: {ml: any; spread: any; total: any; prop: any; parlay: any},
): Promise<ResolveOutcome | null> {
  const legs = bet.legs || [];
  if (!legs.length) return null;
  const results: Result[] = [];
  for (const leg of legs) {
    const legBet: Bet = {...bet, ...leg, id: `${bet.id}_${legs.indexOf(leg)}`, type: leg.type} as Bet;
    const outcome = await resolveBet(supabase, legBet);
    if (!outcome || outcome.result === 'Pending') return {result: 'Pending', detail: 'Parlay: leg still pending', source: 'parlay_composite'};
    results.push(outcome.result);
  }
  // Parlay math: any Loss = Loss; all Wins = Win; any Push cancels that leg but
  // don't kill the parlay (simplification — most books recalculate odds); any
  // Void = whole parlay voids
  if (results.some(r => r === 'Loss')) return {result: 'Loss', detail: `Parlay: ${results.join(', ')}`, source: 'parlay_composite'};
  if (results.some(r => r === 'Void')) return {result: 'Void', detail: `Parlay voided (leg void)`, source: 'parlay_composite'};
  if (results.every(r => r === 'Win' || r === 'Push')) {
    const allWin = results.every(r => r === 'Win');
    return {result: allWin ? 'Win' : 'Push', detail: `Parlay legs: ${results.join(', ')}`, source: 'parlay_composite'};
  }
  return {result: 'Pending', detail: 'Parlay mixed', source: 'parlay_composite'};
}

// ─── Public entry ───────────────────────────────────────────────────────
export async function resolveBet(supabase: SupabaseClient, bet: Bet): Promise<ResolveOutcome | null> {
  if (bet.result && bet.result !== 'Pending') return null;
  const sport = String(bet.sport || '').toUpperCase();
  const type = String(bet.type || '').toLowerCase();

  if (type === 'parlay') {
    return _resolveParlay(supabase, bet, {ml: _resolveML, spread: _resolveSpread, total: _resolveTotal, prop: _resolveProp, parlay: null});
  }

  if (type === 'prop') {
    return _resolveProp(supabase, sport, bet);
  }

  // ML / Spread / Total — need game result
  const parsed = _parseMatchup(bet.matchup);
  if (!parsed) return null;
  const game = await _fetchServerGameResult(supabase, sport, parsed.away, parsed.home, bet.game_date);
  if (!game) return null;

  if (type === 'ml' || type === 'moneyline') return _resolveML(bet.pick, game, bet.matchup);
  if (type === 'spread' || type === 'rl' || type === 'runline') return _resolveSpread(bet.pick, game, bet.matchup);
  if (type === 'total') return _resolveTotal(bet.pick, game);

  return null;
}

/** Batch resolver — used by autoDetectResults sweep in the app. */
export async function resolveAllPending(
  supabase: SupabaseClient, bets: Bet[],
): Promise<Array<{bet: Bet; outcome: ResolveOutcome}>> {
  const pending = bets.filter(b => b.result === 'Pending');
  const out: Array<{bet: Bet; outcome: ResolveOutcome}> = [];
  for (const bet of pending) {
    try {
      const outcome = await resolveBet(supabase, bet);
      if (outcome && outcome.result !== 'Pending') out.push({bet, outcome});
    } catch (e) {
      // Non-fatal — bet stays pending, next sweep tries again
    }
  }
  return out;
}
