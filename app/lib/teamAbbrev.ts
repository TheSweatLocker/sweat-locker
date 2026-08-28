/**
 * Shared team-abbreviation map (2026-08-25).
 *
 * Extracted from GameDetailV2 so any surface rendering team codes uses
 * the same canonical 2-3 letter abbreviation. Prevents bugs like the
 * prop chart .slice(0,3) picking up 'San' for San Diego when the correct
 * abbreviation is 'SD'.
 */

export const TEAM_ABBREV: Record<string, string> = {
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
  // NFL
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
};

// Common short-form aliases that show up in various feeds (opponent codes,
// shortened writeups, roster strings). Kept alongside the full-name map.
const ALIASES: Record<string, string> = {
  // MLB short forms
  'Diamondbacks': 'ARI', 'Braves': 'ATL', 'Orioles': 'BAL', 'Red Sox': 'BOS',
  'Cubs': 'CHC', 'White Sox': 'CWS', 'Reds': 'CIN', 'Guardians': 'CLE',
  'Rockies': 'COL', 'Tigers': 'DET', 'Astros': 'HOU', 'Royals': 'KC',
  'Angels': 'LAA', 'Dodgers': 'LAD', 'Marlins': 'MIA', 'Brewers': 'MIL',
  'Twins': 'MIN', 'Mets': 'NYM', 'Yankees': 'NYY', 'Phillies': 'PHI',
  'Pirates': 'PIT', 'Padres': 'SD', 'Giants': 'SF', 'Mariners': 'SEA',
  'Cardinals': 'STL', 'Rays': 'TB', 'Rangers': 'TEX', 'Blue Jays': 'TOR',
  'Nationals': 'WSH',
  // City-only forms
  'San Diego': 'SD', 'San Francisco': 'SF', 'Kansas City': 'KC',
  'Los Angeles': 'LAA',  // ambiguous — default to Angels; caller should prefer full name
  'New York': 'NYM',     // ambiguous — default to Mets
  'Chicago': 'CHC',      // ambiguous — default to Cubs
  'Tampa Bay': 'TB',
  // Common alt spellings
  'ATH': 'ATH', 'A\'s': 'ATH', 'Athletics': 'ATH',
  'SFG': 'SF', 'SDP': 'SD', 'KCR': 'KC', 'CHW': 'CWS',
  // 2026-08-28: NCAAF Top 25 + tomorrow's Week 0 slate. Without these
  // the tendencies card was showing 'Carolina' for UNC via split-and-pop
  // fallback. Expand as we add sports/weeks. When key isn't found, abbrev()
  // falls back to first-3-chars uppercase — still ugly for CFB teams.
  'North Carolina': 'UNC', 'NC State': 'NCST', 'TCU': 'TCU',
  'Alabama': 'BAMA', 'Georgia': 'UGA', 'Texas': 'TEX', 'Ohio State': 'OSU',
  'Michigan': 'MICH', 'Notre Dame': 'ND', 'Penn State': 'PSU',
  'Oklahoma': 'OU', 'LSU': 'LSU', 'Tennessee': 'TENN', 'Auburn': 'AUB',
  'Florida': 'FLA', 'Florida State': 'FSU', 'Miami': 'MIA', 'Clemson': 'CLEM',
  'Oregon': 'ORE', 'Washington': 'WASH', 'USC': 'USC', 'UCLA': 'UCLA',
  'Utah': 'UTAH', 'Wisconsin': 'WIS', 'Iowa': 'IOWA', 'Nebraska': 'NEB',
  'Missouri': 'MIZZ', 'Arkansas': 'ARK', 'Kentucky': 'UK', 'Ole Miss': 'MISS',
  'Mississippi State': 'MSST', 'Vanderbilt': 'VAN', 'Kansas State': 'KSU',
  'Iowa State': 'ISU', 'Baylor': 'BAY', 'Texas Tech': 'TTU', 'Oklahoma State': 'OKST',
  'BYU': 'BYU', 'Cincinnati': 'CIN', 'Houston': 'HOU', 'UCF': 'UCF',
  'Virginia': 'UVA', 'Wake Forest': 'WAKE', 'Duke': 'DUKE', 'Georgia Tech': 'GT',
  'Louisville': 'LOU', 'Pittsburgh': 'PITT', 'Syracuse': 'SYR', 'Boston College': 'BC',
  'Virginia Tech': 'VT', 'North Carolina State': 'NCST',
  'Stanford': 'STAN', 'Hawaii': 'HAW', 'San Jose State': 'SJSU',
  'New Mexico State': 'NMSU', 'Memphis': 'MEM', 'UNLV': 'UNLV',
};

/**
 * Canonical 2-3 letter team abbreviation for any team-name-ish string.
 * Falls back to first 3 chars if unknown, uppercased.
 */
export function abbrev(name?: string | null): string {
  if (!name) return '';
  const n = String(name).trim();
  if (!n) return '';
  // Direct hit on full name
  if (TEAM_ABBREV[n]) return TEAM_ABBREV[n];
  // Alias hit
  if (ALIASES[n]) return ALIASES[n];
  // Try last-word (nickname) match against full-name map
  const last = n.split(' ').pop() || '';
  if (ALIASES[last]) return ALIASES[last];
  // Already an abbreviation (2-3 uppercase chars)?
  if (/^[A-Z]{2,3}$/.test(n)) return n;
  // Fallback: first 3 chars, uppercased
  return n.slice(0, 3).toUpperCase();
}
