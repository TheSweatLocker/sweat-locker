/**
 * Shared handicapper-source persona map (2026-08-25).
 *
 * "The X" naming per project_the_x_naming_convention_824 +
 * feedback_tos_scrub_source_names. Zero raw source-name exposure in
 * user-facing surfaces. Any component rendering a handicapper picker
 * imports personaFor() from here — no more source name leaks in
 * HandicappersRow, ExternalPicksPanel, Public Splits, etc.
 *
 * Persona assignments are v1 based on source reputation; recompute
 * from 30d pick-pattern data once tracker matures.
 */

const SOURCE_LABEL: Record<string, string> = {
  action:       'The Book',        // market-tracker, data-heavy
  dimers:       'The Grinder',     // algo-driven high-vol
  covers:       'The Volume',      // big daily output
  vsin:         'The Pulse',       // sharp-market analysis
  pickswise:    'The Chalk',       // favorite-heavy
  pickdawgz:    'The Dog',         // underdog specialist
  bettingpros:  'The Spread',      // broad coverage aggregator
  docsports:    'The Lock',        // high-conviction premium
  cbs:          'The Consensus',   // mainstream editorial
  oddsshark:    'The Line',        // line-movement-first
  fangraphs:    'The Nerd',        // sabermetric (playful)
  ballparkpal:  'The Park',        // park-factor specialist
  scp:          'The Fade',        // contrarian trap-setter
  sbr:          'The Room',        // sportsbook-review aggregator
  betfirm:      'The Sharp',       // sharp-money tracker
  tonyspicks:   'The Play',        // premium daily plays
  // Public-money split sources (rendered as anonymized "Split N" elsewhere
  // — this map is a safety net if a raw source string slips into a
  // handicapper surface by accident).
  oddscrowd:    'The Money',       // money-flow / public splits
  fadereport:   'The Splits',      // fade-signal source
  cleatz:       'The Signal',      // sharp-signal source
  scoresandodds:'The Ticket',      // 4th public-splits source (added 8/25)
};

/**
 * Persona name for a raw source string. Falls back to "The Source"
 * for unknown sources so nothing ever leaks a bare vendor name.
 */
export function personaFor(source?: string | null): string {
  if (!source) return 'The Source';
  const key = String(source).toLowerCase().trim();
  return SOURCE_LABEL[key] || 'The Source';
}

/**
 * Returns true if the given source string has a known persona mapping.
 * Useful when hiding a leg entirely when its source isn't scrub-safe.
 */
export function hasPersona(source?: string | null): boolean {
  if (!source) return false;
  return !!SOURCE_LABEL[String(source).toLowerCase().trim()];
}

/**
 * Safety-net scrubber for free-form prose strings that come from the
 * backend (signal_sources.display_prose_template, jerry synthesis,
 * flip audit notes). Replaces any recognized raw source name with its
 * persona so nothing leaks through. Case-insensitive, whole-word only
 * so we don't hit team names.
 */
const SCRUB_REGEX = new RegExp(
  '\\b(' + Object.keys({
    action: 1, dimers: 1, covers: 1, vsin: 1, pickswise: 1, pickdawgz: 1,
    bettingpros: 1, docsports: 1, cbs: 1, oddsshark: 1, fangraphs: 1,
    ballparkpal: 1, scp: 1, sbr: 1, betfirm: 1, tonyspicks: 1,
    oddscrowd: 1, fadereport: 1, cleatz: 1, scoresandodds: 1,
  }).join('|') + ')\\b',
  'gi'
);

export function scrubSourceNames(prose?: string | null): string {
  if (!prose) return '';
  return String(prose).replace(SCRUB_REGEX, (m) => personaFor(m));
}
