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
  // 2026-09-10: Saturday-slate NCAAF fill so abbrev() doesn't fall through
  // to first-3-char slice ("South Florida" → "SOU") or worse, wrong-team
  // last-word collision ("South Florida" → "Florida" → "FLA"). Every team
  // playing this weekend gets an explicit entry. Long-term this should be
  // backend-driven via ncaaf_team_display table (v1.0.1 refactor).
  'South Florida': 'USF', 'Appalachian State': 'APP', 'App State': 'APP',
  'Virginia Tech Hokies': 'VT', 'Old Dominion': 'ODU',
  'East Carolina': 'ECU', 'Army': 'ARMY', 'Navy': 'NAVY', 'Air Force': 'AF',
  'Florida Atlantic': 'FAU', 'Florida International': 'FIU', 'FIU': 'FIU',
  'Charlotte': 'CHAR', 'Marshall': 'MRSH', 'Rice': 'RICE', 'Tulane': 'TUL',
  'Tulsa': 'TULSA', 'SMU': 'SMU', 'Temple': 'TEM', 'Kansas': 'KU',
  'Illinois': 'ILL', 'Illinois State': 'ILST', 'Northern Illinois': 'NIU',
  'Western Illinois': 'WIU', 'Northwestern': 'NW', 'Purdue': 'PUR',
  'Rutgers': 'RUT', 'Maryland': 'MD', 'Indiana': 'IND',
  'Michigan State': 'MSU', 'Minnesota': 'MINN', 'Ohio': 'OHIO',
  'Ball State': 'BALL', 'Bowling Green': 'BGSU', 'Buffalo': 'BUF',
  'Central Michigan': 'CMU', 'Eastern Michigan': 'EMU', 'Kent State': 'KENT',
  'Miami (OH)': 'M-OH', 'Toledo': 'TOL', 'Akron': 'AKRN', 'Massachusetts': 'MASS',
  'UMass': 'MASS', 'Connecticut': 'UCONN', 'UConn': 'UCONN',
  'Boise State': 'BSU', 'Colorado State': 'CSU', 'Fresno State': 'FRES',
  'Nevada': 'NEV', 'San Diego State': 'SDSU', 'Utah State': 'USU',
  'Wyoming': 'WYO', 'New Mexico': 'UNM', 'North Texas': 'UNT',
  'UTEP': 'UTEP', 'UTSA': 'UTSA', 'Sam Houston': 'SHSU', 'Sam Houston State': 'SHSU',
  'Missouri State': 'MOST', 'Jacksonville State': 'JVST', 'Liberty': 'LIB',
  'Kennesaw State': 'KENN', 'Delaware': 'DEL',
  // 'Delaware State' is defined once, in the FCS block below, as 'DELS'.
  'James Madison': 'JMU', 'Coastal Carolina': 'CCU', 'Georgia Southern': 'GASO',
  'Georgia State': 'GAST', 'Arkansas State': 'ARST', 'Louisiana': 'ULL',
  'Louisiana-Lafayette': 'ULL', 'Louisiana Tech': 'LATECH', 'Louisiana Monroe': 'ULM',
  'Troy': 'TROY', 'South Alabama': 'USA', 'Southern Miss': 'USM',
  'Southern Mississippi': 'USM', 'Middle Tennessee': 'MTSU', 'Middle Tennessee State': 'MTSU',
  // FCS common opponents (Saturday's non-conference blowouts)
  'North Dakota State': 'NDSU', 'South Dakota State': 'SDSU-D1',
  'Montana State': 'MTST', 'Montana': 'MONT', 'Sacramento State': 'SAC',
  'Weber State': 'WEB', 'Cal Poly': 'CP', 'Idaho State': 'IDST',
  'Southern Utah': 'SUU', 'Northern Colorado': 'UNCO', 'UC Davis': 'UCD',
  'UT Martin': 'UTM', 'Tennessee State': 'TNST', 'Tennessee Tech': 'TNTC',
  'East Tennessee State': 'ETSU', 'Chattanooga': 'CHAT', 'Furman': 'FUR',
  'Mercer': 'MERC', 'Samford': 'SAM', 'Wofford': 'WOF',
  'Western Carolina': 'WCU', 'The Citadel': 'CIT', 'VMI': 'VMI',
  'Colgate': 'COLG', 'Cornell': 'CORN', 'Dartmouth': 'DART', 'Harvard': 'HARV',
  'Yale': 'YALE', 'Princeton': 'PRIN', 'Penn': 'PENN', 'Brown': 'BRWN',
  'Bucknell': 'BUCK', 'Fordham': 'FORD', 'Georgetown': 'GTWN',
  'Holy Cross': 'HC', 'Lafayette': 'LAF', 'Lehigh': 'LEH',
  'Villanova': 'NOVA', 'Wagner': 'WAG', 'Sacred Heart': 'SHU',
  'Central Connecticut': 'CCSU', 'Robert Morris': 'RMU', 'Stonehill': 'STON',
  'Duquesne': 'DUQ', 'Merrimack': 'MER', 'St. Francis (PA)': 'SFP',
  'LIU': 'LIU', 'Monmouth': 'MON', 'Stony Brook': 'STBK',
  'Elon': 'ELON', 'Hampton': 'HAMP', 'Howard': 'HOW', 'Norfolk State': 'NSU',
  'Morgan State': 'MORG', 'Delaware State': 'DELS',
  'Grambling': 'GRAM', 'Prairie View A&M': 'PVAMU', 'Southern': 'SU',
  'Alabama State': 'ALST', 'Alabama A&M': 'AAMU', 'Alcorn State': 'ALCN',
  'Florida A&M': 'FAMU', 'Jackson State': 'JKST', 'Mississippi Valley State': 'MVSU',
  'Bethune-Cookman': 'BCU', 'Texas Southern': 'TXSO', 'Arkansas-Pine Bluff': 'UAPB',
  'Campbell': 'CAMP', 'Charleston Southern': 'CHSO', 'Gardner-Webb': 'GWU',
  'Presbyterian': 'PRES', 'Richmond': 'RICH', 'William & Mary': 'WM',
  'Towson': 'TOW', 'Albany': 'ALB', 'Rhode Island': 'URI',
  'Maine': 'ME', 'New Hampshire': 'UNH',
  'Mercyhurst': 'MHU', 'Lindenwood': 'LIN',
  'Portland State': 'PDXST', 'Idaho': 'IDA', 'Eastern Washington': 'EWU',
  'Northern Arizona': 'NAU',
  'Abilene Christian': 'ACU', 'Incarnate Word': 'UIW', 'Lamar': 'LAM',
  'McNeese': 'MCN', 'Nicholls': 'NICH', 'Northwestern State': 'NWST',
  'Southeastern Louisiana': 'SELA', 'Stephen F. Austin': 'SFA',
  'Houston Christian': 'HCU', 'Tarleton State': 'TAR',
  'Youngstown State': 'YSU',
  'Indiana State': 'INST', 'South Dakota': 'USD',
  'Western Michigan': 'WMU',

  // 2026-09-26 · COLLISION FIXES.
  // Andy, on a Houston card: "9/4 vs ORE · W 33-20 · -21 — a 21-point
  // favorite beating 'ORE' 33-20. If that's Oregon, neither team on this
  // card plays them. Likely an opponent-abbreviation collision."
  //
  // It was. Houston beat OREGON STATE 33-20 on 9/5. "Oregon State" had no
  // entry, so abbrev() fell through to slice(0,3) and produced ORE — the
  // code for Oregon. Auditing all 237 NCAAF teams currently in play found
  // 14 more pairs of DIFFERENT teams sharing one code, including TEX for
  // Texas / Texas A&M / Texas State and UVA for Virginia / West Virginia.
  // A wrong opponent chip in a game log is not cosmetic: it is the line
  // users read to judge whether a result was impressive.
  'Oregon State': 'ORST',
  'Arizona State': 'ASU',
  'Texas A&M': 'TAMU', 'Texas State': 'TXST',
  'West Virginia': 'WVU',
  'West Georgia': 'UWG',
  'North Alabama': 'UNA',
  'Eastern Kentucky': 'EKU', 'Western Kentucky': 'WKU',
  'Eastern Illinois': 'EIU', 'Southern Illinois': 'SIU',
  'Northern Iowa': 'UNI',
  'Louisiana Ragin Cajuns': 'ULL',
  'Mercyhurst Lakers': 'MRCY',
  'North Carolina A&T': 'NCAT', 'North Carolina A&T Aggies': 'NCAT',
  'North Dakota': 'UND', 'North Dakota State Bison': 'NDSU',
  'Southeast Missouri State': 'SEMO',
  'Houston Baptist Huskies': 'HCU',
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

  // 2026-09-26: the old fallback was slice(0,3), which maps "Oregon
  // State" and "Oregon" to the same ORE — and did, on a live card. The
  // explicit entries above cover every team currently in play, but new
  // opponents arrive every week (FCS non-conference especially), so the
  // fallback itself must not silently merge two schools.
  //
  // Two patterns cause nearly all of it, and both are recoverable from
  // the name: a "<School> State" suffix, and a directional prefix. Both
  // now encode the distinguishing word instead of discarding it.
  const words = n.split(/\s+/).filter(Boolean);
  if (words.length >= 2 && /^state$/i.test(words[words.length - 1])) {
    // "Oregon State" -> ORST, "Ball State" -> BALST
    return (words[0].slice(0, 3) + 'ST').toUpperCase();
  }
  const DIRECTION: Record<string, string> = {
    north: 'N', south: 'S', east: 'E', west: 'W',
    northern: 'N', southern: 'S', eastern: 'E', western: 'W',
    central: 'C', middle: 'M',
  };
  if (words.length >= 2 && DIRECTION[words[0].toLowerCase()]) {
    // "West Virginia" -> WVIR, distinct from "Virginia" -> VIR
    return (DIRECTION[words[0].toLowerCase()] + words[1].slice(0, 3)).toUpperCase();
  }
  // Fallback: first 3 chars, uppercased
  return n.slice(0, 3).toUpperCase();
}
