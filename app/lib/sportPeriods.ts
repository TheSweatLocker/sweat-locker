/**
 * Sport-cadence-aware period helpers.
 *
 * Different sports have completely different rhythms — "yesterday" works
 * for MLB/NBA/NCAAB/NHL but not for NFL (weekly) or UFC (event-based).
 *
 * This module centralizes the logic so receipts / recap components can
 * just say "give me the relevant date range for SPORT + PERIOD" and get
 * the right answer regardless of cadence.
 *
 * Date math is all in ET because that's how the pipeline normalizes
 * game_date — keeps app and backend in sync.
 */

export type Sport = 'MLB' | 'NBA' | 'NCAAB' | 'NFL' | 'NHL' | 'UFC';

export type Period =
  | 'yesterday'    // single ET date (MLB/NBA/NCAAB/NHL)
  | 'last_slate'   // most recent slate (NFL: Sun-Mon-Thu group)
  | 'last_event'   // most recent UFC card
  | 'last_7d'      // rolling 7-day window
  | 'last_30d'     // rolling 30-day window
  | 'season';      // current season-to-date

export type DateRange = {
  startDate: string;     // YYYY-MM-DD (ET)
  endDate: string;       // YYYY-MM-DD (ET, inclusive)
  label: string;         // user-facing label e.g. "Yesterday" or "Last Week"
  cadence: 'daily' | 'weekly' | 'event';
};

/**
 * ET date helper. Pipeline uses ET-truncated dates throughout, so this
 * matches what gets stored on game_date / bet_date fields.
 */
function etDate(offsetDays = 0): string {
  const utc = Date.now();
  const etOffsetMs = -4 * 60 * 60 * 1000; // EDT; -5 for EST. Close enough year-round for our purposes.
  const d = new Date(utc + etOffsetMs + offsetDays * 24 * 60 * 60 * 1000);
  return d.toISOString().slice(0, 10);
}

/**
 * Default period for a sport based on its cadence.
 * Receipts components use this as initial state; user can override with chip selector.
 */
export function getDefaultPeriod(sport: Sport): Period {
  if (sport === 'NFL') return 'last_slate';
  if (sport === 'UFC') return 'last_event';
  return 'yesterday';
}

/**
 * Period selector options available per sport. NFL doesn't show "yesterday"
 * since most days have no games; UFC shows event-based options.
 */
export function getAvailablePeriods(sport: Sport): Period[] {
  if (sport === 'NFL') return ['last_slate', 'last_7d', 'last_30d', 'season'];
  if (sport === 'UFC') return ['last_event', 'last_30d', 'season'];
  return ['yesterday', 'last_7d', 'last_30d', 'season'];
}

/**
 * Convert (sport, period) to a concrete date range + label.
 *
 * NFL "last_slate" rolls back to the most recent Mon-Sun cluster of
 * gamedays. UFC "last_event" is approximated as the most recent
 * Saturday (refine later when ufc_events table is wired).
 */
export function getDateRange(sport: Sport, period: Period): DateRange {
  const today = etDate(0);
  const yesterday = etDate(-1);

  if (period === 'yesterday') {
    return {
      startDate: yesterday,
      endDate: yesterday,
      label: 'Yesterday',
      cadence: 'daily',
    };
  }

  if (period === 'last_slate' && sport === 'NFL') {
    // NFL slate = the most recent Thu-Sun-Mon cluster.
    // Approximation: find the most recent Monday, range = (that Monday - 4 days) to that Monday.
    const now = new Date(today + 'T12:00:00Z');
    const dow = now.getUTCDay(); // 0=Sun, 1=Mon, ..., 6=Sat
    let daysBackToMonday: number;
    if (dow === 0) daysBackToMonday = 6;       // Sun → last Mon is 6 days back
    else if (dow === 1) daysBackToMonday = 7;  // Mon → last Mon is a week back (today's MNF not yet played at AM)
    else daysBackToMonday = dow - 1;           // Tue=1, Wed=2, Thu=3, Fri=4, Sat=5
    const slateEnd = etDate(-daysBackToMonday);
    const slateStart = etDate(-daysBackToMonday - 4); // Thu = 4 days before Mon
    return {
      startDate: slateStart,
      endDate: slateEnd,
      label: 'Last Slate',
      cadence: 'weekly',
    };
  }

  if (period === 'last_event' && sport === 'UFC') {
    // Most-recent-Saturday approximation. When ufc_events table ships, this
    // should query for the actual most-recent event_date.
    const now = new Date(today + 'T12:00:00Z');
    const dow = now.getUTCDay();
    let daysBackToSat: number;
    if (dow === 6) daysBackToSat = 7;  // It's Saturday — last event was a week ago (today's not done)
    else if (dow === 0) daysBackToSat = 1;
    else daysBackToSat = dow + 1;
    const eventDate = etDate(-daysBackToSat);
    return {
      startDate: eventDate,
      endDate: eventDate,
      label: 'Last Event',
      cadence: 'event',
    };
  }

  if (period === 'last_7d') {
    return {
      startDate: etDate(-7),
      endDate: yesterday,
      label: 'Last 7 Days',
      cadence: 'daily',
    };
  }

  if (period === 'last_30d') {
    return {
      startDate: etDate(-30),
      endDate: yesterday,
      label: 'Last 30 Days',
      cadence: 'daily',
    };
  }

  // 'season' — for v1.0 just hard-code season start by sport. v1.x: pull from a season_meta table.
  const seasonStart = SEASON_START[sport] || etDate(-90);
  return {
    startDate: seasonStart,
    endDate: yesterday,
    label: 'Season',
    cadence: 'daily',
  };
}

/**
 * Hardcoded season starts. Replace with season_meta table once we have
 * multi-year support. For launch (May 2026) these are accurate for the
 * current MLB season; other sports plug in when they ship.
 */
const SEASON_START: Record<Sport, string> = {
  MLB: '2026-03-27',
  NBA: '2026-10-21',
  NCAAB: '2026-11-04',
  NFL: '2026-09-04',
  NHL: '2026-10-07',
  UFC: '2026-01-01',
};

/**
 * Lowercase canonical for database queries. The `sport` column in
 * mlb_tier_calibration uses lowercase.
 */
export function sportDb(sport: Sport): string {
  return sport.toLowerCase();
}

/**
 * Tables that hold per-sport pick-resolution data. Used by RecapCard.
 * MLB has dedicated tables; other sports will get analog tables as
 * their pipelines ship.
 */
export function getResolvedPropsTable(sport: Sport): string | null {
  if (sport === 'MLB') return 'mlb_pipeline_props';
  if (sport === 'NCAAB') return null;     // No NCAAB props in v1 — by design
  if (sport === 'NBA') return 'nba_pipeline_props';  // Future
  if (sport === 'NFL') return 'nfl_pipeline_props';  // Future
  return null;
}

/**
 * Whether this sport has any game data available yet (i.e., its pipeline
 * has started writing results). Lets the recap show "Coming soon" cards
 * instead of zeros for not-yet-launched sports.
 */
export function isSportLive(sport: Sport): boolean {
  // v1.0: MLB only. NCAAB ships Nov. Others ship in v1.x.
  if (sport === 'MLB') return true;
  if (sport === 'NCAAB') {
    const today = etDate(0);
    return today >= SEASON_START.NCAAB;
  }
  return false;
}
