import AsyncStorage from '@react-native-async-storage/async-storage';
import { createClient } from '@supabase/supabase-js';
import axios from 'axios';
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, KeyboardAvoidingView, Linking, Modal, Platform, RefreshControl, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { Circle, Defs, G, LinearGradient, Path, Rect, Stop, Svg, Line as SvgLine, Text as SvgText } from 'react-native-svg';

const ODDS_API_KEY = process.env.EXPO_PUBLIC_ODDS_API_KEY;
const ANTHROPIC_API_KEY = process.env.EXPO_PUBLIC_ANTHROPIC_API_KEY;
const BDL_API_KEY = process.env.EXPO_PUBLIC_BDL_API_KEY;
const KENPOM_KEY = process.env.EXPO_PUBLIC_KENPOM_KEY;
const supabase = createClient(
  process.env.EXPO_PUBLIC_SUPABASE_URL,
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY
);
const HRB = 'Hard Rock';
const HRB_COLOR = '#FFB800';

const SPORTS = ['NBA', 'NFL', 'NHL', 'MLB', 'NCAAB', 'NCAAF', 'UFC'];
const BET_TYPES = ['Spread', 'Moneyline', 'Total (O/U)', 'Player Prop', 'Parlay'];
const BOOKS = ['Hard Rock', 'DraftKings', 'FanDuel', 'ESPN Bet', 'BetMGM', 'Caesars', 'Bet365'];
const RESULTS = ['Pending', 'Win', 'Loss', 'Push'];
const SPORT_KEYS = {
  NBA:'basketball_nba', NFL:'americanfootball_nfl', NHL:'icehockey_nhl',
  MLB:'baseball_mlb', NCAAB:'basketball_ncaab', NCAAF:'americanfootball_ncaaf',
  UFC:'mma_mixed_martial_arts',
};
const SPORT_EMOJI = { NBA:'🏀', NFL:'🏈', NHL:'🏒', MLB:'⚾', NCAAB:'🏀', NCAAF:'🏈', UFC:'🥊' };
const BOOKMAKER_MAP = {
  'draftkings':'DraftKings','fanduel':'FanDuel','espnbet':'ESPN Bet',
  'betmgm':'BetMGM','caesars':'Caesars','bet365':'Bet365',
  'williamhill_us':'Caesars','hardrockbet':'Hard Rock','hardrock':'Hard Rock',
};
// Odds API uses slightly different names for some MLB teams — map alternates to canonical MLB Stats API names
const MLB_TEAM_ALIASES: Record<string, string[]> = {
  'Arizona Diamondbacks': ['Arizona Diamondbacks', 'ARI Diamondbacks', 'AZ Diamondbacks'],
  'Cleveland Guardians': ['Cleveland Guardians', 'Cleveland Indians'],
  'Oakland Athletics': ['Oakland Athletics', "Oakland A's", 'Athletics'],
  'Los Angeles Angels': ['Los Angeles Angels', 'LA Angels', 'Anaheim Angels'],
  'Chicago White Sox': ['Chicago White Sox', 'Chi White Sox'],
  'Tampa Bay Rays': ['Tampa Bay Rays', 'TB Rays'],
  'St. Louis Cardinals': ['St. Louis Cardinals', 'St Louis Cardinals', 'STL Cardinals'],
  'San Francisco Giants': ['San Francisco Giants', 'SF Giants'],
  'San Diego Padres': ['San Diego Padres', 'SD Padres'],
  'New York Yankees': ['New York Yankees', 'NY Yankees'],
  'New York Mets': ['New York Mets', 'NY Mets'],
  'Los Angeles Dodgers': ['Los Angeles Dodgers', 'LA Dodgers'],
  'Kansas City Royals': ['Kansas City Royals', 'KC Royals'],
};
const PROP_MARKETS = {
  NBA:['player_points','player_rebounds','player_assists','player_threes'],
  NFL:['player_pass_yds','player_rush_yds','player_reception_yds','player_receptions'],
  MLB:['batter_hits','batter_home_runs','pitcher_strikeouts'],
  NHL:['player_goals','player_assists','player_shots_on_goal'],
  UFC:['fighter_total_rounds','fighter_ko_tko','fighter_decision','fighter_method_of_victory'],
};
const PROP_LABELS = {
  player_points:'Points', player_rebounds:'Rebounds', player_assists:'Assists',
  player_threes:'3-Pointers', player_pass_yds:'Pass Yards', player_rush_yds:'Rush Yards',
  player_reception_yds:'Rec Yards', player_receptions:'Receptions',
  batter_hits:'Hits', batter_home_runs:'Home Runs', pitcher_strikeouts:'Strikeouts',
  player_goals:'Goals', player_shots_on_goal:'Shots on Goal', player_anytime_td:'Anytime TD',
  batter_total_bases:'Total Bases', batter_rbis:'RBIs', batter_runs_scored:'Runs Scored', batter_strikeouts:'Strikeouts',
  fighter_total_rounds:'Total Rounds', fighter_ko_tko:'KO/TKO',
  fighter_decision:'Decision', fighter_method_of_victory:'Method of Victory',
};
const STORAGE_KEY = 'sweatlocker_bets';
const JERRY_HISTORY_KEY = 'sweatlocker_jerry_history';
const SETTINGS_KEY = 'sweatlocker_settings';

const americanToDecimal = (american) => {
  const odds = parseFloat(american);
  if (isNaN(odds)) return 1;
  return odds > 0 ? (odds/100)+1 : (100/Math.abs(odds))+1;
};
const decimalToAmerican = (decimal) => {
  if (decimal >= 2) return '+'+Math.round((decimal-1)*100);
  return ''+Math.round(-100/(decimal-1));
};
const calcParlayOdds = (legs) => {
  if (!legs.length) return 0;
  return legs.reduce((acc,leg) => acc * americanToDecimal(leg.oddsSign+leg.odds), 1);
};
const impliedProb = (decimal) => {
  if (decimal <= 1) return 0;
  return ((1/decimal)*100).toFixed(1);
};
const impliedProbRaw = (american) => {
  const dec = americanToDecimal(american);
  if (dec <= 1) return 0;
  return (1/dec)*100;
};
const vigFreeProb = (probs) => {
  const total = probs.reduce((a,b) => a+b, 0);
  return probs.map(p => (p/total)*100);
};
const calcEV = (bookOdds, marketProb) => {
  const dec = americanToDecimal(bookOdds);
  const winProb = marketProb/100;
  return ((winProb*(dec-1)) - (1-winProb)) * 100;
};
const calcKelly = (odds, evPct, bankrollUnits = 20) => {
  if(!odds || !evPct || evPct <= 0) return null;
  const dec = americanToDecimal(odds);
  if(dec <= 1) return null;
  const winProb = (evPct / 100) + (1 / dec) - (evPct / 100) * (1 / dec);
  const kellyFull = ((winProb * (dec - 1)) - (1 - winProb)) / (dec - 1);
  const kellyQuarter = kellyFull * 0.25;
  const suggestedUnits = Math.max(0.5, Math.min(3, kellyQuarter * bankrollUnits));
  return {
    kellyPct: (kellyFull * 100).toFixed(1),
    quarterKellyPct: (kellyQuarter * 100).toFixed(1),
    suggestedUnits: suggestedUnits.toFixed(1),
  };
};
// Generate deterministic matchup data from team names
const getMatchupData = (game) => {
  if (!game) return null;
  const away = stripMascot(game.away_team);
  const home = stripMascot(game.home_team);
  const seed = (away.length * 7 + home.length * 13) % 100;

  // Big Money data
  const awayBetPct = 30 + (seed % 40);
  const homeBetPct = 100 - awayBetPct;
  const awayMoneyPct = Math.max(5, awayBetPct - 10 - (seed % 20));
  const homeMoneyPct = 100 - awayMoneyPct;
  const diff = Math.abs(homeMoneyPct - homeBetPct);
  const sharpSide = homeMoneyPct > homeBetPct ? home : away;
  const sharpRating = diff > 20 ? 'Strong' : diff > 10 ? 'Good' : 'Moderate';

  // Team stats (efficiency rankings 1-30 for NBA)
  const awayOff = 1 + (seed * 3 % 30);
  const awayDef = 1 + (seed * 7 % 30);
  const homeOff = 1 + (seed * 11 % 30);
  const homeDef = 1 + (seed * 17 % 30);

  const statCategories = [
    {label:'Off Rating', away: awayOff, home: homeOff},
    {label:'Def Rating', away: awayDef, home: homeDef},
    {label:'Pace', away: 1+(seed*5%30), home: 1+(seed*9%30)},
    {label:'FG%', away: 1+(seed*2%30), home: 1+(seed*6%30)},
    {label:'3PT%', away: 1+(seed*4%30), home: 1+(seed*8%30)},
    {label:'Rebounds', away: 1+(seed*6%30), home: 1+(seed*12%30)},
    {label:'Assists', away: 1+(seed*8%30), home: 1+(seed*14%30)},
    {label:'Turnovers', away: 1+(seed*10%30), home: 1+(seed*16%30)},
  ];

  // Recent schedule
  const opponents = ['LAL','BOS','GSW','MIL','PHX','DEN','MIA','CHI','ATL','DAL'];
  const makeGames = (teamSeed) => Array.from({length:5},(_,i)=>{
    const win = (teamSeed + i*3) % 3 !== 0;
    const atsWin = (teamSeed + i*5) % 3 !== 0;
    const ouOver = (teamSeed + i*7) % 2 === 0;
    const spread = (((teamSeed + i) % 10) - 5).toFixed(1);
    const total = (210 + (teamSeed + i*3) % 20).toFixed(1);
    return {
      date: `${2}/${20-i*3}`,
      opp: opponents[(teamSeed+i) % opponents.length],
      home: i % 2 === 0,
      score: win ? `${85+(teamSeed%15)+i*2}-${78+(teamSeed%10)}` : `${78+(teamSeed%10)}-${85+(teamSeed%15)+i*2}`,
      win, atsWin, ouOver,
      spread: atsWin ? `+${Math.abs(spread)}` : `-${Math.abs(spread)}`,
      total,
    };
  });

  const awayGames = makeGames(away.length + seed);
  const homeGames = makeGames(home.length + seed);
  const h2hGames = makeGames(seed + 5);

  // Situational ATS records
  const makeSitRec = (w,l,p=0) => ({w,l,p,pct:w+l>0?((w/(w+l))*100).toFixed(0):'—'});
  const awaySit = {
    overallATS: makeSitRec(8+(seed%8), 6+(seed%6), seed%2),
    last10ATS: makeSitRec(4+(seed%5), 6-(seed%5)),
    awayATS: makeSitRec(4+(seed%4), 4+(seed%4)),
    dogATS: makeSitRec(3+(seed%5), 3+(seed%4)),
    overallOU: makeSitRec(7+(seed%7), 7+(seed%7)),
    last10OU: makeSitRec(4+(seed%5), 6-(seed%5)),
  };
  const homeSit = {
    overallATS: makeSitRec(6+(seed%7), 8+(seed%7), seed%2),
    last10ATS: makeSitRec(5+(seed%4), 5-(seed%4)),
    homeATS: makeSitRec(5+(seed%4), 3+(seed%4)),
    favATS: makeSitRec(4+(seed%5), 4+(seed%4)),
    overallOU: makeSitRec(7+(seed%6), 7+(seed%8)),
    last10OU: makeSitRec(5+(seed%4), 5-(seed%4)),
  };

  return {
    away, home,
    awayBetPct, homeBetPct,
    awayMoneyPct, homeMoneyPct,
    diff, sharpSide, sharpRating,
    statCategories,
    awayGames, homeGames, h2hGames,
    awaySit, homeSit,
  };
};
 
const normalizeTeamName = (name) => {
    if(!name) return name;
    const map = {
      'UConn': 'Connecticut',
      'UCF': 'Central Florida',
      'UNLV': 'Nevada Las Vegas',
      'Ole Miss': 'Mississippi',
      'Pitt': 'Pittsburgh',
      'Miami FL': 'Miami',
      'Long Island University': 'LIU',
      'LIU Brooklyn': 'LIU',
      'GW': 'George Washington',
      'JMU': 'James Madison',
      'USF': 'South Florida',
      'FIU': 'Florida International',
      'FAU': 'Florida Atlantic',
      'UMass': 'Massachusetts',
      'UNI': 'Northern Iowa',
      'App State': 'Appalachian State',
      'NC State': 'NC State',
      'UNC': 'North Carolina',
      'UNCW': 'UNC Wilmington',
      'Louisiana Ragin Cajuns': 'Louisiana',
      'James Madison Dukes': 'James Madison',
      'Fordham Rams': 'Fordham',
      'La Salle Explorers': 'La Salle',
      'Wagner Seahawks': 'Wagner',
      'Central Connecticut St Blue Devils': 'Central Connecticut',
      'Central Connecticut St': 'Central Connecticut',
      'Chicago St Cougars': 'Chicago State',
      'Chicago St': 'Chicago State',
      'LIU Sharks': 'LIU',
      'GW Revolutionaries': 'George Washington',
      'Stonehill Skyhawks': 'Stonehill',
      'Le Moyne Dolphins': 'Le Moyne',
      'North Florida Ospreys': 'North Florida',
      'West Georgia Wolves': 'West Georgia',
      'Depaul Blue Demons': 'Depaul',
      'Old Dominion Monarchs': 'Old Dominion',
      'Georgia Southern Eagles': 'Georgia Southern',
      'Florida State Seminoles': 'Florida State',
      'Florida State': 'Florida State',
      'FSU': 'Florida State',
      'Florida Gators': 'Florida',
      'Florida A&M Rattlers': 'Florida AM',
      'Florida A&M': 'Florida AM',
      'FAMU': 'Florida AM',
      'Florida Atlantic Owls': 'Florida Atlantic',
      'Florida Gulf Coast Eagles': 'Florida Gulf Coast',
      'Florida International Panthers': 'Florida International',
      'South Florida Bulls': 'South Florida',
      'Central Florida Knights': 'Central Florida',
      'North Florida Ospreys': 'North Florida',
      'Miami Hurricanes': 'Miami FL',
      'Miami (OH) Redhawks': 'Miami OH',
      'Mississippi State Bulldogs': 'Mississippi State',
      'Michigan State Spartans': 'Michigan State',
      'Kansas State Wildcats': 'Kansas State',
      'Iowa State Cyclones': 'Iowa State',
      'Ohio State Buckeyes': 'Ohio State',
      'Arizona State Sun Devils': 'Arizona State',
      'San Diego State Aztecs': 'San Diego State',
      'Boise State Broncos': 'Boise State',
      'Colorado State Rams': 'Colorado State',
      'Utah State Aggies': 'Utah State',
      'New Mexico State Aggies': 'New Mexico State',
      'Fresno State Bulldogs': 'Fresno State',
      'Sacramento State Hornets': 'Sacramento State',
      'Montana State Bobcats': 'Montana State',
      'North Dakota State Bison': 'North Dakota State',
      'South Dakota State Jackrabbits': 'South Dakota State',
      'Weber State Wildcats': 'Weber State',
      'Portland State Vikings': 'Portland State',
      'Georgia State Panthers': 'Georgia State',
      'Kennesaw State Owls': 'Kennesaw State',
      'Texas State Bobcats': 'Texas State',
      'Tarleton State Texans': 'Tarleton State',
      'NC State Wolfpack': 'NC State',
      'Penn State Nittany Lions': 'Penn State',
      'Oklahoma State Cowboys': 'Oklahoma State',
      'Oregon State Beavers': 'Oregon State',
      'Washington State Cougars': 'Washington State',
      'UNLV Rebels': 'UNLV',
      'Kent State Golden Flashes': 'Kent State',
      'uconn': 'Connecticut',
      'UConn Huskies': 'Connecticut',
      'Tulane Green Wave': 'Tulane',
    };
    let normalized = name;
    Object.keys(map).forEach(key => {
      if(normalized.toLowerCase() === key.toLowerCase()) normalized = map[key];
    });
    return normalized;
    };

const fuzzyMatchTeam = (name, list, nameKey) => {
  if(!name || !list || !list.length) return null;
  //console.log('FUZZY INPUT:',name);
  
const EXACT_MAP = {
  // A
  'abilene christian': 'Abilene Christian',
  'abilene christian wildcats': 'Abilene Christian',
  'air force': 'Air Force',
  'air force falcons': 'Air Force',
  'akron': 'Akron',
  'akron zips': 'Akron',
  'alabama': 'Alabama',
  'alabama crimson tide': 'Alabama',
  'alabama a&m': 'Alabama A&M',
  'alabama a&m bulldogs': 'Alabama A&M',
  'alabama am': 'Alabama A&M',
  'alabama state': 'Alabama St.',
  'alabama st': 'Alabama St.',
  'alabama state hornets': 'Alabama St.',
  'albany': 'Albany',
  'albany great danes': 'Albany',
  'alcorn state': 'Alcorn St.',
  'alcorn st': 'Alcorn St.',
  'alcorn state braves': 'Alcorn St.',
  'american': 'American',
  'american eagles': 'American',
  'american university': 'American',
  'appalachian state': 'App State',
  'appalachian st': 'App State',
  'app state': 'App State',
  'app state mountaineers': 'App State',
  'arizona': 'Arizona',
  'arizona wildcats': 'Arizona',
  'arizona state': 'Arizona St.',
  'arizona st': 'Arizona St.',
  'arizona state sun devils': 'Arizona St.',
  'arkansas': 'Arkansas',
  'arkansas razorbacks': 'Arkansas',
  'arkansas pine bluff': 'Ark. Pine Bluff',
  'ark pine bluff': 'Ark. Pine Bluff',
  'arkansas pine bluff golden lions': 'Ark. Pine Bluff',
  'arkansas state': 'Arkansas St.',
  'arkansas st': 'Arkansas St.',
  'arkansas state red wolves': 'Arkansas St.',
  'army': 'Army',
  'army black knights': 'Army',
  'auburn': 'Auburn',
  'auburn tigers': 'Auburn',
  'austin peay': 'Austin Peay',
  'austin peay governors': 'Austin Peay',
  // B
  'ball state': 'Ball St.',
  'ball st': 'Ball St.',
  'ball state cardinals': 'Ball St.',
  'baylor': 'Baylor',
  'baylor bears': 'Baylor',
  'bellarmine': 'Bellarmine',
  'bellarmine knights': 'Bellarmine',
  'belmont': 'Belmont',
  'belmont bruins': 'Belmont',
  'bethune cookman': 'Bethune-Cookman',
  'bethune-cookman': 'Bethune-Cookman',
  'bethune cookman wildcats': 'Bethune-Cookman',
  'binghamton': 'Binghamton',
  'binghamton bearcats': 'Binghamton',
  'boise state': 'Boise St.',
  'boise st': 'Boise St.',
  'boise state broncos': 'Boise St.',
  'boston college': 'Boston College',
  'boston college eagles': 'Boston College',
  'boston university': 'Boston University',
  'boston u': 'Boston University',
  'bowling green': 'Bowling Green',
  'bowling green falcons': 'Bowling Green',
  'bradley': 'Bradley',
  'bradley braves': 'Bradley',
  'brown': 'Brown',
  'brown bears': 'Brown',
  'bryant': 'Bryant',
  'bryant bulldogs': 'Bryant',
  'bucknell': 'Bucknell',
  'bucknell bison': 'Bucknell',
  'buffalo': 'Buffalo',
  'buffalo bulls': 'Buffalo',
  'butler': 'Butler',
  'butler bulldogs': 'Butler',
  // C
  'cal': 'California',
  'california': 'California',
  'california golden bears': 'California',
  'cal baptist': 'Cal Baptist',
  'california baptist': 'Cal Baptist',
  'cal poly': 'Cal Poly',
  'cal poly mustangs': 'Cal Poly',
  'cal state bakersfield': 'CS Bakersfield',
  'cs bakersfield': 'CS Bakersfield',
  'cal state fullerton': 'CS Fullerton',
  'cs fullerton': 'CS Fullerton',
  'cal state northridge': 'CS Northridge',
  'cs northridge': 'CS Northridge',
  'csun': 'CS Northridge',
  'cal state northridge': 'CS Northridge',
'cal state northridge matadors': 'CS Northridge',
'california state northridge': 'CS Northridge',
'cal state bakersfield': 'CS Bakersfield',
'california state bakersfield': 'CS Bakersfield',
'cal state bakersfield roadrunners': 'CS Bakersfield',
'csub': 'CS Bakersfield',
'cal state fullerton': 'CS Fullerton',
'california state fullerton': 'CS Fullerton',
'cal state fullerton titans': 'CS Fullerton',
'csuf': 'CS Fullerton',
  'campbell': 'Campbell',
  'campbell camels': 'Campbell',
  'canisius': 'Canisius',
  'canisius golden griffins': 'Canisius',
  'central arkansas': 'Central Arkansas',
  'central arkansas bears': 'Central Arkansas',
  'central connecticut': 'Central Conn.',
  'central connecticut state': 'Central Conn.',
  'central conn': 'Central Conn.',
  'central florida': 'UCF',
  'central florida knights': 'UCF',
  'ucf': 'UCF',
  'ucf knights': 'UCF',
  'central michigan': 'Central Michigan',
  'central michigan chippewas': 'Central Michigan',
  'charleston': 'Charleston',
  'charleston cougars': 'Charleston',
  'charlotte': 'Charlotte',
  'charlotte 49ers': 'Charlotte',
  'chattanooga': 'Chattanooga',
  'chattanooga mocs': 'Chattanooga',
  'chicago state': 'Chicago St.',
  'chicago st': 'Chicago St.',
  'chicago state cougars': 'Chicago St.',
  'cincinnati': 'Cincinnati',
  'cincinnati bearcats': 'Cincinnati',
  'citadel': 'The Citadel',
  'the citadel': 'The Citadel',
  'clemson': 'Clemson',
  'clemson tigers': 'Clemson',
  'cleveland state': 'Cleveland St.',
  'cleveland st': 'Cleveland St.',
  'cleveland state vikings': 'Cleveland St.',
  'coastal carolina': 'Coastal Carolina',
  'coastal carolina chanticleers': 'Coastal Carolina',
  'colgate': 'Colgate',
  'colgate raiders': 'Colgate',
  'college of charleston': 'Charleston',
  'colorado': 'Colorado',
  'colorado buffaloes': 'Colorado',
  'colorado state': 'Colorado St.',
  'colorado st': 'Colorado St.',
  'colorado state rams': 'Colorado St.',
  'columbia': 'Columbia',
  'columbia lions': 'Columbia',
  'connecticut': 'Connecticut',
  'uconn': 'Connecticut',
  'connecticut huskies': 'Connecticut',
  'uconn huskies': 'Connecticut',
  'coppin state': 'Coppin St.',
  'coppin st': 'Coppin St.',
  'coppin state eagles': 'Coppin St.',
  'cornell': 'Cornell',
  'cornell big red': 'Cornell',
  'creighton': 'Creighton',
  'creighton bluejays': 'Creighton',
  // D
  'dartmouth': 'Dartmouth',
  'dartmouth big green': 'Dartmouth',
  'davidson': 'Davidson',
  'davidson wildcats': 'Davidson',
  'dayton': 'Dayton',
  'dayton flyers': 'Dayton',
  'delaware': 'Delaware',
  'delaware fightin blue hens': 'Delaware',
  'delaware state': 'Delaware St.',
  'delaware st': 'Delaware St.',
  'delaware state hornets': 'Delaware St.',
  'denver': 'Denver',
  'denver pioneers': 'Denver',
  'depaul': 'DePaul',
  'depaul blue demons': 'DePaul',
  'detroit mercy': 'Detroit Mercy',
  'detroit': 'Detroit Mercy',
  'detroit mercy titans': 'Detroit Mercy',
  'drake': 'Drake',
  'drake bulldogs': 'Drake',
  'drexel': 'Drexel',
  'drexel dragons': 'Drexel',
  'duke': 'Duke',
  'duke blue devils': 'Duke',
  'duquesne': 'Duquesne',
  'duquesne dukes': 'Duquesne',
  // E
  'east carolina': 'East Carolina',
  'east carolina pirates': 'East Carolina',
  'east tennessee state': 'ETSU',
  'etsu': 'ETSU',
  'east tennessee state buccaneers': 'ETSU',
  'eastern illinois': 'Eastern Illinois',
  'eastern illinois panthers': 'Eastern Illinois',
  'eastern kentucky': 'Eastern Kentucky',
  'eastern kentucky colonels': 'Eastern Kentucky',
  'eastern michigan': 'Eastern Michigan',
  'eastern michigan eagles': 'Eastern Michigan',
  'eastern washington': 'Eastern Washington',
  'eastern washington eagles': 'Eastern Washington',
  'elon': 'Elon',
  'elon phoenix': 'Elon',
  'evansville': 'Evansville',
  'evansville purple aces': 'Evansville',
  // F
  'fairfield': 'Fairfield',
  'fairfield stags': 'Fairfield',
  'fairleigh dickinson': 'F. Dickinson',
  'f. dickinson': 'F. Dickinson',
  'fairleigh dickinson knights': 'F. Dickinson',
  'fdu': 'F. Dickinson',
  'florida': 'Florida',
  'florida gators': 'Florida',
  'florida a&m': 'Florida A&M',
  'florida am': 'Florida A&M',
  'famu': 'Florida A&M',
  'florida a&m rattlers': 'Florida A&M',
  'florida atlantic': 'Florida Atlantic',
  'fau': 'Florida Atlantic',
  'florida atlantic owls': 'Florida Atlantic',
  'florida gulf coast': 'FL Gulf Coast',
  'fgcu': 'FL Gulf Coast',
  'florida gulf coast eagles': 'FL Gulf Coast',
  'fl gulf coast': 'FL Gulf Coast',
  'florida international': 'FIU',
  'fiu': 'FIU',
  'florida international panthers': 'FIU',
  'florida state': 'Florida St.',
  'florida st': 'Florida St.',
  'fsu': 'Florida St.',
  'florida state seminoles': 'Florida St.',
  'fordham': 'Fordham',
  'fordham rams': 'Fordham',
  'fresno state': 'Fresno St.',
  'fresno st': 'Fresno St.',
  'fresno state bulldogs': 'Fresno St.',
  'furman': 'Furman',
  'furman paladins': 'Furman',
  // G
  'gardner webb': 'Gardner-Webb',
  'gardner-webb': 'Gardner-Webb',
  'gardner webb runnin bulldogs': 'Gardner-Webb',
  'george mason': 'George Mason',
  'george mason patriots': 'George Mason',
  'george washington': 'G. Washington',
  'g. washington': 'G. Washington',
  'george washington revolutionaries': 'G. Washington',
  'georgetown': 'Georgetown',
  'georgetown hoyas': 'Georgetown',
  'georgia': 'Georgia',
  'georgia bulldogs': 'Georgia',
  'georgia southern': 'Georgia Southern',
  'georgia southern eagles': 'Georgia Southern',
  'georgia state': 'Georgia St.',
  'georgia st': 'Georgia St.',
  'georgia state panthers': 'Georgia St.',
  'georgia tech': 'Georgia Tech',
  'georgia tech yellow jackets': 'Georgia Tech',
  'gonzaga': 'Gonzaga',
  'gonzaga bulldogs': 'Gonzaga',
  'grambling': 'Grambling',
  'grambling state': 'Grambling',
  'grambling state tigers': 'Grambling',
  'grand canyon': 'Grand Canyon',
  'grand canyon antelopes': 'Grand Canyon',
  'green bay': 'Green Bay',
  'green bay phoenix': 'Green Bay',
  // H
  'hampton': 'Hampton',
  'hampton pirates': 'Hampton',
  'hartford': 'Hartford',
  'hartford hawks': 'Hartford',
  'harvard': 'Harvard',
  'harvard crimson': 'Harvard',
  'hawaii': "Hawai'i",
  "hawaii": "Hawai'i",
  "hawai'i": "Hawai'i",
  'hawaii rainbow warriors': "Hawai'i",
  'high point': 'High Point',
  'high point panthers': 'High Point',
  'hofstra': 'Hofstra',
  'hofstra pride': 'Hofstra',
  'holy cross': 'Holy Cross',
  'holy cross crusaders': 'Holy Cross',
  'houston': 'Houston',
  'houston cougars': 'Houston',
  'houston christian': 'Houston Christian',
  'houston christian huskies': 'Houston Christian',
  'howard': 'Howard',
  'howard bison': 'Howard',
  // I
  'idaho': 'Idaho',
  'idaho vandals': 'Idaho',
  'idaho state': 'Idaho St.',
  'idaho st': 'Idaho St.',
  'idaho state bengals': 'Idaho St.',
  'illinois': 'Illinois',
  'illinois fighting illini': 'Illinois',
  'illinois state': 'Illinois St.',
  'illinois st': 'Illinois St.',
  'illinois state redbirds': 'Illinois St.',
  'incarnate word': 'Incarnate Word',
  'incarnate word cardinals': 'Incarnate Word',
  'indiana': 'Indiana',
  'indiana hoosiers': 'Indiana',
  'indiana state': 'Indiana St.',
  'indiana st': 'Indiana St.',
  'indiana state sycamores': 'Indiana St.',
  'iona': 'Iona',
  'iona gaels': 'Iona',
  'iowa': 'Iowa',
  'iowa hawkeyes': 'Iowa',
  'iowa state': 'Iowa St.',
  'iowa st': 'Iowa St.',
  'iowa state cyclones': 'Iowa St.',
  // J
  'jackson state': 'Jackson St.',
  'jackson st': 'Jackson St.',
  'jackson state tigers': 'Jackson St.',
  'jacksonville': 'Jacksonville',
  'jacksonville dolphins': 'Jacksonville',
  'jacksonville state': 'Jacksonville St.',
  'jacksonville st': 'Jacksonville St.',
  'jacksonville state gamecocks': 'Jacksonville St.',
  'james madison': 'James Madison',
  'james madison dukes': 'James Madison',
  // K
  'kansas': 'Kansas',
  'kansas jayhawks': 'Kansas',
  'kansas city': 'UMKC',
  'umkc': 'UMKC',
  'kansas state': 'Kansas St.',
  'kansas st': 'Kansas St.',
  'kansas state wildcats': 'Kansas St.',
  'kennesaw state': 'Kennesaw St.',
  'kennesaw st': 'Kennesaw St.',
  'kennesaw state owls': 'Kennesaw St.',
  'kent state': 'Kent St.',
  'kent st': 'Kent St.',
  'kent state golden flashes': 'Kent St.',
  'kentucky': 'Kentucky',
  'kentucky wildcats': 'Kentucky',
  // L
  'lamar': 'Lamar',
  'lamar cardinals': 'Lamar',
  'le moyne': 'Le Moyne',
  'le moyne dolphins': 'Le Moyne',
  'lehigh': 'Lehigh',
  'lehigh mountain hawks': 'Lehigh',
  'liberty': 'Liberty',
  'liberty flames': 'Liberty',
  'lindenwood': 'Lindenwood',
  'lindenwood lions': 'Lindenwood',
  'lipscomb': 'Lipscomb',
  'lipscomb bisons': 'Lipscomb',
  'little rock': 'Little Rock',
  'arkansas little rock': 'Little Rock',
  'little rock trojans': 'Little Rock',
  'long beach state': 'Long Beach St.',
  'long beach st': 'Long Beach St.',
  'long beach state beach': 'Long Beach St.',
  'long island university': 'LIU',
  'liu': 'LIU',
  'longwood': 'Longwood',
  'longwood lancers': 'Longwood',
  'louisiana': 'Louisiana',
  'louisiana ragin cajuns': 'Louisiana',
  'ul lafayette': 'Louisiana',
  'louisiana lafayette': 'Louisiana',
  'louisiana monroe': 'UL Monroe',
  'ul monroe': 'UL Monroe',
  'louisiana monroe warhawks': 'UL Monroe',
  'louisiana state': 'LSU',
  'lsu': 'LSU',
  'lsu tigers': 'LSU',
  'louisville': 'Louisville',
  'louisville cardinals': 'Louisville',
  'loyola chicago': 'Loyola Chicago',
  'loyola chicago ramblers': 'Loyola Chicago',
  'loyola maryland': 'Loyola Maryland',
  'loyola maryland greyhounds': 'Loyola Maryland',
  'loyola marymount': 'Loyola Marymount',
  'lmu': 'Loyola Marymount',
  'loyola marymount lions': 'Loyola Marymount',
  // M
  'maine': 'Maine',
  'maine black bears': 'Maine',
  'manhattan': 'Manhattan',
  'manhattan jaspers': 'Manhattan',
  'marist': 'Marist',
  'marist red foxes': 'Marist',
  'marquette': 'Marquette',
  'marquette golden eagles': 'Marquette',
  'marshall': 'Marshall',
  'marshall thundering herd': 'Marshall',
  'maryland': 'Maryland',
  'maryland terrapins': 'Maryland',
  'mcneese': 'McNeese St.',
  'mcneese state': 'McNeese St.',
  'mcneese st': 'McNeese St.',
  'mcneese state cowboys': 'McNeese St.',
  'memphis': 'Memphis',
  'memphis tigers': 'Memphis',
  'mercer': 'Mercer',
  'mercer bears': 'Mercer',
  'merrimack': 'Merrimack',
  'merrimack warriors': 'Merrimack',
  'miami fl': 'Miami FL',
  'miami (fl)': 'Miami FL',
  'miami hurricanes': 'Miami FL',
  'miami florida': 'Miami FL',
  'miami oh': 'Miami OH',
  'miami ohio': 'Miami OH',
  'miami (oh)': 'Miami OH',
  'miami ohio redhawks': 'Miami OH',
  'michigan': 'Michigan',
  'michigan wolverines': 'Michigan',
  'michigan state': 'Michigan St.',
  'michigan st': 'Michigan St.',
  'michigan state spartans': 'Michigan St.',
  'middle tennessee': 'Middle Tennessee',
  'middle tennessee state': 'Middle Tennessee',
  'mtsu': 'Middle Tennessee',
  'middle tennessee blue raiders': 'Middle Tennessee',
  'milwaukee': 'Milwaukee',
  'uw milwaukee': 'Milwaukee',
  'milwaukee panthers': 'Milwaukee',
  'minnesota': 'Minnesota',
  'minnesota golden gophers': 'Minnesota',
  'mississippi': 'Mississippi',
  'ole miss': 'Mississippi',
  'ole miss rebels': 'Mississippi',
  'mississippi rebels': 'Mississippi',
  'mississippi state': 'Mississippi St.',
  'mississippi st': 'Mississippi St.',
  'mississippi state bulldogs': 'Mississippi St.',
  'mississippi valley state': 'Miss. Valley St.',
  'miss valley state': 'Miss. Valley St.',
  'miss. valley st': 'Miss. Valley St.',
  'mississippi valley state delta devils': 'Miss. Valley St.',
  'missouri': 'Missouri',
  'missouri tigers': 'Missouri',
  'missouri state': 'Missouri St.',
  'missouri st': 'Missouri St.',
  'missouri state bears': 'Missouri St.',
  'monmouth': 'Monmouth',
  'monmouth hawks': 'Monmouth',
  'montana': 'Montana',
  'montana grizzlies': 'Montana',
  'montana state': 'Montana St.',
  'montana st': 'Montana St.',
  'montana state bobcats': 'Montana St.',
  'morehead state': 'Morehead St.',
  'morehead st': 'Morehead St.',
  'morehead state eagles': 'Morehead St.',
  'morgan state': 'Morgan St.',
  'morgan st': 'Morgan St.',
  'morgan state bears': 'Morgan St.',
  'mount st. marys': "Mt. St. Mary's",
  "mount st mary's": "Mt. St. Mary's",
  "mt. st. mary's": "Mt. St. Mary's",
  "mount st. mary's mountaineers": "Mt. St. Mary's",
  'murray state': 'Murray St.',
  'murray st': 'Murray St.',
  'murray state racers': 'Murray St.',
  // N
  'navy': 'Navy',
  'navy midshipmen': 'Navy',
  'nc state': 'N.C. State',
  'NC State': 'NC State',
'N.C. State': 'NC State',
'NC State Wolfpack': 'NC State',
'North Carolina State': 'NC State',
'North Carolina State Wolfpack': 'NC State',
  'north carolina state': 'N.C. State',
  'n.c. state': 'N.C. State',
  'nc state wolfpack': 'N.C. State',
  'north carolina state wolfpack': 'N.C. State',
  'nebraska': 'Nebraska',
  'nebraska cornhuskers': 'Nebraska',
  'nebraska omaha': 'Omaha',
  'omaha': 'Omaha',
  'nevada': 'Nevada',
  'nevada wolf pack': 'Nevada',
  'nevada las vegas': 'UNLV',
  'unlv': 'UNLV',
  'unlv rebels': 'UNLV',
  'new hampshire': 'New Hampshire',
  'new hampshire wildcats': 'New Hampshire',
  'new mexico': 'New Mexico',
  'new mexico lobos': 'New Mexico',
  'new mexico state': 'New Mexico St.',
  'new mexico st': 'New Mexico St.',
  'new mexico state aggies': 'New Mexico St.',
  'niagara': 'Niagara',
  'niagara purple eagles': 'Niagara',
  'nicholls': 'Nicholls St.',
  'nicholls state': 'Nicholls St.',
  'nicholls st': 'Nicholls St.',
  'nicholls state colonels': 'Nicholls St.',
  'njit': 'NJIT',
  'norfolk state': 'Norfolk St.',
  'norfolk st': 'Norfolk St.',
  'norfolk state spartans': 'Norfolk St.',
  'north alabama': 'North Alabama',
  'north alabama lions': 'North Alabama',
  'north carolina': 'North Carolina',
  'unc': 'North Carolina',
  'north carolina tar heels': 'North Carolina',
  'north carolina a&t': 'N.C. A&T',
  'nc a&t': 'N.C. A&T',
  'north carolina at': 'N.C. A&T',
  'north carolina a&t aggies': 'N.C. A&T',
  'north carolina central': 'NC Central',
  'nc central': 'NC Central',
  'north carolina central eagles': 'NC Central',
  'north dakota': 'North Dakota',
  'north dakota fighting hawks': 'North Dakota',
  'north dakota state': 'North Dakota St.',
  'north dakota st': 'North Dakota St.',
  'north dakota state bison': 'North Dakota St.',
  'north florida': 'North Florida',
  'north florida ospreys': 'North Florida',
  'north texas': 'North Texas',
  'north texas mean green': 'North Texas',
  'northeastern': 'Northeastern',
  'northeastern huskies': 'Northeastern',
  'northern arizona': 'Northern Arizona',
  'northern arizona lumberjacks': 'Northern Arizona',
  'northern colorado': 'Northern Colorado',
  'northern colorado bears': 'Northern Colorado',
  'northern illinois': 'Northern Illinois',
  'northern illinois huskies': 'Northern Illinois',
  'northern iowa': 'Northern Iowa',
  'northern iowa panthers': 'Northern Iowa',
  'northern kentucky': 'Northern Kentucky',
  'northern kentucky norse': 'Northern Kentucky',
  'northwestern': 'Northwestern',
  'northwestern wildcats': 'Northwestern',
  'northwestern state': 'Northwestern St.',
  'northwestern st': 'Northwestern St.',
  'northwestern state demons': 'Northwestern St.',
  'notre dame': 'Notre Dame',
  'notre dame fighting irish': 'Notre Dame',
  // O
  'ohio': 'Ohio',
  'ohio bobcats': 'Ohio',
  'ohio state': 'Ohio St.',
  'ohio st': 'Ohio St.',
  'ohio state buckeyes': 'Ohio St.',
  'oklahoma': 'Oklahoma',
  'oklahoma sooners': 'Oklahoma',
  'oklahoma state': 'Oklahoma St.',
  'oklahoma st': 'Oklahoma St.',
  'oklahoma state cowboys': 'Oklahoma St.',
  'old dominion': 'Old Dominion',
  'old dominion monarchs': 'Old Dominion',
  'oral roberts': 'Oral Roberts',
  'oral roberts golden eagles': 'Oral Roberts',
  'oregon': 'Oregon',
  'oregon ducks': 'Oregon',
  'oregon state': 'Oregon St.',
  'oregon st': 'Oregon St.',
  'oregon state beavers': 'Oregon St.',
  // P
  'pacific': 'Pacific',
  'pacific tigers': 'Pacific',
  'penn': 'Penn',
  'pennsylvania': 'Penn',
  'penn quakers': 'Penn',
  'penn state': 'Penn St.',
  'penn st': 'Penn St.',
  'penn state nittany lions': 'Penn St.',
  'pepperdine': 'Pepperdine',
  'pepperdine waves': 'Pepperdine',
  'pittsburgh': 'Pittsburgh',
  'pitt': 'Pittsburgh',
  'pittsburgh panthers': 'Pittsburgh',
  'portland': 'Portland',
  'portland pilots': 'Portland',
  'portland state': 'Portland St.',
  'portland st': 'Portland St.',
  'portland state vikings': 'Portland St.',
  'prairie view a&m': 'Prairie View',
  'prairie view': 'Prairie View',
  'prairie view am': 'Prairie View',
  'prairie view a&m panthers': 'Prairie View',
  'presbyterian': 'Presbyterian',
  'presbyterian blue hose': 'Presbyterian',
  'princeton': 'Princeton',
  'princeton tigers': 'Princeton',
  'providence': 'Providence',
  'providence friars': 'Providence',
  'purdue': 'Purdue',
  'purdue boilermakers': 'Purdue',
  'purdue fort wayne': 'Purdue Fort Wayne',
  'purdue fort wayne mastodons': 'Purdue Fort Wayne',
  // Q
  'quinnipiac': 'Quinnipiac',
  'quinnipiac bobcats': 'Quinnipiac',
  // R
  'radford': 'Radford',
  'radford highlanders': 'Radford',
  'rhode island': 'Rhode Island',
  'rhode island rams': 'Rhode Island',
  'rice': 'Rice',
  'rice owls': 'Rice',
  'richmond': 'Richmond',
  'richmond spiders': 'Richmond',
  'rider': 'Rider',
  'rider broncs': 'Rider',
  'robert morris': 'Robert Morris',
  'robert morris colonials': 'Robert Morris',
  'rutgers': 'Rutgers',
  'rutgers scarlet knights': 'Rutgers',
  // S
  'sacramento state': 'Sacramento St.',
  'sacramento st': 'Sacramento St.',
  'sacramento state hornets': 'Sacramento St.',
  'sacred heart': 'Sacred Heart',
  'sacred heart pioneers': 'Sacred Heart',
  'saint francis pa': 'Saint Francis',
  'saint francis': 'Saint Francis',
  'saint francis red flash': 'Saint Francis',
  "saint joseph's": "Saint Joseph's",
  'saint josephs': "Saint Joseph's",
  "st. joseph's": "Saint Joseph's",
  "saint joseph's hawks": "Saint Joseph's",
  "saint mary's": "Saint Mary's",
  'saint marys': "Saint Mary's",
  "st. mary's": "Saint Mary's",
  "saint mary's gaels": "Saint Mary's",
  "saint peter's": "Saint Peter's",
  'saint peters': "Saint Peter's",
  "st. peter's": "Saint Peter's",
  "saint peter's peacocks": "Saint Peter's",
  'sam houston': 'Sam Houston',
  'sam houston state': 'Sam Houston',
  'sam houston bearkats': 'Sam Houston',
  'samford': 'Samford',
  'samford bulldogs': 'Samford',
  'san diego': 'San Diego',
  'san diego toreros': 'San Diego',
  'san diego state': 'San Diego St.',
  'san diego st': 'San Diego St.',
  'san diego state aztecs': 'San Diego St.',
  'san francisco': 'San Francisco',
  'san francisco dons': 'San Francisco',
  'san jose state': 'San Jose St.',
  'san jose st': 'San Jose St.',
  'san josé state': 'San Jose St.',
  'san josé st': 'San Jose St.',
  'santa barbara': 'UC Santa Barbara',
  'uc santa barbara': 'UC Santa Barbara',
  'ucsb': 'UC Santa Barbara',
  'seattle': 'Seattle U',
  'seattle u': 'Seattle U',
  'seattle redhawks': 'Seattle U',
  'seton hall': 'Seton Hall',
  'seton hall pirates': 'Seton Hall',
  'siena': 'Siena',
  'siena saints': 'Siena',
  'smu': 'SMU',
  'southern methodist': 'SMU',
  'smu mustangs': 'SMU',
  'south alabama': 'South Alabama',
  'south alabama jaguars': 'South Alabama',
  'south carolina': 'South Carolina',
  'south carolina gamecocks': 'South Carolina',
  'south carolina state': 'S. Carolina St.',
  's. carolina st': 'S. Carolina St.',
  'south carolina state bulldogs': 'S. Carolina St.',
  'south dakota': 'South Dakota',
  'south dakota coyotes': 'South Dakota',
  'south dakota state': 'South Dakota St.',
  'south dakota st': 'South Dakota St.',
  'south dakota state jackrabbits': 'South Dakota St.',
  'south florida': 'South Florida',
  'usf': 'South Florida',
  'south florida bulls': 'South Florida',
  'southeastern louisiana': 'SE Louisiana',
  'se louisiana': 'SE Louisiana',
  'southeastern louisiana lions': 'SE Louisiana',
  'southern': 'Southern',
  'southern jaguars': 'Southern',
  'southern university': 'Southern.',
  'southern university jaguars': 'Southern',
  'southern illinois': 'Southern Illinois',
  'southern illinois salukis': 'Southern Illinois',
  'southern indiana': 'Southern Indiana',
  'southern indiana screaming eagles': 'Southern Indiana',
  'southern miss': 'Southern Miss',
  'southern mississippi': 'Southern Miss',
  'southern miss golden eagles': 'Southern Miss',
  'southern utah': 'Southern Utah',
  'southern utah thunderbirds': 'Southern Utah',
  'st johns': "St. John's",
  "st. john's": "St. John's",
  "st. john's red storm": "St. John's",
  'st bonaventure': 'St. Bonaventure',
  'st. bonaventure': 'St. Bonaventure',
  'st. bonaventure bonnies': 'St. Bonaventure',
  'st francis brooklyn': 'St. Francis BKN',
  'st. francis bkn': 'St. Francis BKN',
  'st. francis brooklyn terriers': 'St. Francis BKN',
  'stanford': 'Stanford',
  'stanford cardinal': 'Stanford',
  'stephen f. austin': 'SF Austin',
  'stephen f austin': 'SF Austin',
  'sf austin': 'SF Austin',
  'stephen f. austin lumberjacks': 'SF Austin',
  'stetson': 'Stetson',
  'stetson hatters': 'Stetson',
  'stony brook': 'Stony Brook',
  'stony brook seawolves': 'Stony Brook',
  'syracuse': 'Syracuse',
  'syracuse orange': 'Syracuse',
  // T
  'tarleton state': 'Tarleton St.',
  'tarleton st': 'Tarleton St.',
  'tarleton state texans': 'Tarleton St.',
  'tcu': 'TCU',
  'texas christian': 'TCU',
  'tcu horned frogs': 'TCU',
  'temple': 'Temple',
  'temple owls': 'Temple',
  'tennessee': 'Tennessee',
  'tennessee volunteers': 'Tennessee',
  'tennessee martin': 'UT Martin',
  'ut martin': 'UT Martin',
  'tennessee martin skyhawks': 'UT Martin',
  'tennessee state': 'Tennessee St.',
  'tennessee st': 'Tennessee St.',
  'tennessee state tigers': 'Tennessee St.',
  'tennessee tech': 'Tennessee Tech',
  'tennessee tech golden eagles': 'Tennessee Tech',
  'texas': 'Texas',
  'texas longhorns': 'Texas',
  'texas am': 'Texas A&M',
  'texas a&m': 'Texas A&M',
  'texas a&m aggies': 'Texas A&M',
  'texas am corpus christi': 'TAM C. Christi',
  'texas a&m corpus christi': 'TAM C. Christi',
  'tam c. christi': 'TAM C. Christi',
  'texas southern': 'Texas Southern',
  'texas southern tigers': 'Texas Southern',
  'texas state': 'Texas State',
  'texas state bobcats': 'Texas State',
  'texas tech': 'Texas Tech',
  'texas tech red raiders': 'Texas Tech',
  'the citadel': 'The Citadel',
  'citadel bulldogs': 'The Citadel',
  'toledo': 'Toledo',
  'toledo rockets': 'Toledo',
  'towson': 'Towson',
  'towson tigers': 'Towson',
  'troy': 'Troy',
  'troy trojans': 'Troy',
  'tulane': 'Tulane',
  'tulane green wave': 'Tulane',
  'tulsa': 'Tulsa',
  'tulsa golden hurricane': 'Tulsa',
  // U
  'uab': 'UAB',
  'alabama birmingham': 'UAB',
  'uab blazers': 'UAB',
  'uc davis': 'UC Davis',
  'uc davis aggies': 'UC Davis',
  'uc irvine': 'UC Irvine',
  'uc irvine anteaters': 'UC Irvine',
  'uc riverside': 'UC Riverside',
  'uc riverside highlanders': 'UC Riverside',
  'uc san diego': 'UC San Diego',
  'ucsd': 'UC San Diego',
  'uc san diego tritons': 'UC San Diego',
  'ucla': 'UCLA',
  'ucla bruins': 'UCLA',
  'umass': 'Massachusetts',
  'massachusetts': 'Massachusetts',
  'umass minutemen': 'Massachusetts',
  'umass lowell': 'UMass Lowell',
  'unc asheville': 'UNC Asheville',
  'unc asheville bulldogs': 'UNC Asheville',
  'unc greensboro': 'UNC Greensboro',
  'unc greensboro spartans': 'UNC Greensboro',
  'unc wilmington': 'UNC Wilmington',
  'unc wilmington seahawks': 'UNC Wilmington',
  'unlv': 'UNLV',
  'unlv rebels': 'UNLV',
  'usc': 'USC',
  'southern california': 'USC',
  'usc trojans': 'USC',
  'utah': 'Utah',
  'utah utes': 'Utah',
  'utah state': 'Utah St.',
  'utah st': 'Utah St.',
  'utah state aggies': 'Utah St.',
  'utah valley': 'Utah Valley',
  'utah valley wolverines': 'Utah Valley',
  'utep': 'UTEP',
  'texas el paso': 'UTEP',
  'utep miners': 'UTEP',
  'utsa': 'UTSA',
  'texas san antonio': 'UTSA',
  'utsa roadrunners': 'UTSA',
  // V
  'valparaiso': 'Valparaiso',
  'valparaiso beacons': 'Valparaiso',
  'vcu': 'VCU',
  'virginia commonwealth': 'VCU',
  'vcu rams': 'VCU',
  'vermont': 'Vermont',
  'vermont catamounts': 'Vermont',
  'villanova': 'Villanova',
  'villanova wildcats': 'Villanova',
  'virginia': 'Virginia',
  'virginia cavaliers': 'Virginia',
  'virginia military institute': 'VMI',
  'vmi': 'VMI',
  'vmi keydets': 'VMI',
  'virginia tech': 'Virginia Tech',
  'virginia tech hokies': 'Virginia Tech',
  // W
  'wagner': 'Wagner',
  'wagner seahawks': 'Wagner',
  'wake forest': 'Wake Forest',
  'wake forest demon deacons': 'Wake Forest',
  'washington': 'Washington',
  'washington huskies': 'Washington',
  'washington state': 'Washington St.',
  'washington st': 'Washington St.',
  'washington state cougars': 'Washington St.',
  'weber state': 'Weber St.',
  'weber st': 'Weber St.',
  'weber state wildcats': 'Weber St.',
  'west georgia': 'West Georgia',
  'west georgia wolves': 'West Georgia',
  'west virginia': 'West Virginia',
  'west virginia mountaineers': 'West Virginia',
  'western carolina': 'Western Carolina',
  'western carolina catamounts': 'Western Carolina',
  'western illinois': 'Western Illinois',
  'western illinois leathernecks': 'Western Illinois',
  'western kentucky': 'Western Kentucky',
  'wku': 'Western Kentucky',
  'western kentucky hilltoppers': 'Western Kentucky',
  'western michigan': 'Western Michigan',
  'western michigan broncos': 'Western Michigan',
  'wichita state': 'Wichita St.',
  'wichita st': 'Wichita St.',
  'wichita state shockers': 'Wichita St.',
  'william & mary': 'William & Mary',
  'william and mary': 'William & Mary',
  'william mary': 'William & Mary',
  'william & mary tribe': 'William & Mary',
  'winthrop': 'Winthrop',
  'winthrop eagles': 'Winthrop',
  'wisconsin': 'Wisconsin',
  'wisconsin badgers': 'Wisconsin',
  'wofford': 'Wofford',
  'wofford terriers': 'Wofford',
  'wright state': 'Wright St.',
  'wright st': 'Wright St.',
  'wright state raiders': 'Wright St.',
  'wyoming': 'Wyoming',
  'wyoming cowboys': 'Wyoming',
  // X
  'xavier': 'Xavier',
  'xavier musketeers': 'Xavier',
  // Y
  'yale': 'Yale',
  'yale bulldogs': 'Yale',
  'youngstown state': 'Youngstown St.',
  'youngstown st': 'Youngstown St.',
  'youngstown state penguins': 'Youngstown St.',
};
  const nameLower = name.toLowerCase().trim();
  //console.log('NAMELOWER:', nameLower, '| MAP HIT:', !!EXACT_MAP[nameLower]);
  if(EXACT_MAP[nameLower]) {
  //console.log('EXACT TARGET:', EXACT_MAP[nameLower]);
  const exactMatch = list.find(item => item[nameKey] && item[nameKey].toLowerCase() === EXACT_MAP[nameLower].toLowerCase());
  //console.log('EXACT MATCH RESULT:', exactMatch?.team);
  if(exactMatch) return exactMatch;
}
  name = stripMascot(normalizeTeamName(name));

  //console.log('fuzzyMatchTeam input after strip:', name);
  if(!name||!list||!list.length) return null;
     const clean = (s) => {
    if(s===null||s===undefined) return '';
    return String(s).toLowerCase()
      .replace(/university|college|tar heels|wolfpack|hawkeyes|buckeyes|hoosiers|razorbacks|crimson tide|fighting|the |of |-/g,'')
      .replace(/ragin|cajuns|seahawks|sharks|revolutionaries|skyhawks|ospreys|wolves|monarchs|explorers|bonnies|dolphins/g,'')
      .replace(/\s+/g,' ').trim();
  };
  const target = clean(name);
  let best = null, bestScore = 0;
  list.forEach(item => {
    if(!item) return;
    const val = item[nameKey];
    if(val===null||val===undefined) return;
    const candidate = clean(val);
    if(!candidate) return;
    if(candidate === target){ best = item; bestScore = 100; return; }
    // Penalize if one has 'st' and other doesn't (Michigan vs Michigan St)
   const targetNorm = target.replace(/\bstate\b/g, 'st');
    const candidateNorm = candidate.replace(/\bstate\b/g, 'st');
    if(targetNorm === candidateNorm){ best = item; bestScore = 100; return; }
    const targetWords = target.split(' ').filter(w=>w.length>2);
    const candidateWords = candidate.split(' ').filter(w=>w.length>2);
    if(!targetWords.length||!candidateWords.length) return;
    const matches = targetWords.filter(w => candidateWords.some(c=>c.includes(w)||w.includes(c)));
    const score = matches.length / Math.max(targetWords.length, candidateWords.length, 1);
   //if(score > 0.2) console.log('Score:', score, target, '->', candidate);
    if(score > bestScore){ bestScore = score; best = item; }
  });
  return bestScore > 0.45 ? best : null;
};
const stripMascot = (teamName) => {
    if(!teamName) return teamName;
    const nicknames = [
      'Blue Devils','Tar Heels','Wildcats','Bulldogs','Tigers','Eagles','Volunteers',
      'Hurricanes','Gators','Seminoles','Nittany Lions','Hoosiers','Boilermakers',
      'Hawkeyes','Badgers','Spartans','Wolverines','Buckeyes','Illini','Huskers',
      'Jayhawks','Mountaineers','Cavaliers','Yellow Jackets','Demon Deacons',
      'Cardinal','Bears','Bruins','Trojans','Ducks','Beavers','Utes','Cougars',
      'Lobos','Cowboys','Longhorns','Sooners','Horned Frogs','Mustangs','Raiders',
      'Red Raiders','Aggies','Miners','Rebels','Wolf Pack','Runnin Rebels',
      'Aztecs','Broncos','Falcons','Rams','Rams','Rams','Panthers','Ravens',
      'Knights','Warriors','Rainbow Warriors','Anteaters','Tritons','Gauchos',
      'Retrievers','Terrapins','Terps','Scarlet Knights','Owls','Rockets',
      'Flashes','Zips','Bobcats','Chippewas','Cardinals','Red Birds','Sycamores',
      'Braves','Norse','Penguins','Fighting Irish','Flyers','Pilots','Gaels',
      'Toreros','Friars','Bonnies','Explorers','Owls','Hawks','Spiders',
      'Billikens','Bluejays','Blue Jays','Musketeers','Bearcats','Ramblers',
      'Crusaders','Purple Eagles','Golden Eagles','Golden Flashes','Golden Gophers',
      'Golden Bears','Mean Green','Red Wolves','Razorbacks','Gamecocks','Paladins',
      'Flames','Monarchs','Dukes','Colonials','Patriots','Seawolves','River Hawks',
      'Retrievers','Greyhounds','Quakers','Falcons','Panthers','Rams','Lions','Cardinals','Penguins','Retrievers',
      'Bisons','Lumberjacks','Flames','Thunderbirds','Norse','Mavericks',
      'Highlanders','Beacons','Roadrunners','Owls','Flyers','Quakers',
      'Friars','Toreros','Gaels','Pilots','Waves','Sycamores','Leathernecks',
      'Govs','Skyhawks','Golden Eagles','Red Foxes','Peacocks','Purple Eagles','Big Red','Crimson','Catamounts',
      'Keydets','Hokies','Orange','Shockers','Big Green','Ephs','Mammoths','Lords','Yeomen',
      'Mules','Bears','Bison','Lions','Royals','Saints','Pilots','Waves',
      'Matadors','49ers','Roadrunners','Lumberjacks','Jacks','Bucks','Penmen',
      'Penguins','Ospreys','Dolphins','Sharks','Storm','Thunder','Heat',
      'Celtics','Lakers','Clippers','Warriors','Nets','Knicks','Bulls',
      'Pacers','Pistons','Bucks','Hawks','Hornets','Magic','Heat','Raptors',
      'Cavaliers','Pistons','76ers','Celtics',
    ];
     let result = teamName
      .replace(/Scarlet Knights/gi, '')
      .replace(/Ragin' Cajuns/gi, '')
      .replace(/Ragin Cajuns/gi, '')
      .replace(/Blue Devils/gi, '')
      .replace(/Tar Heels/gi, '')
      .replace(/Nittany Lions/gi, '')
      .replace(/Mean Green/gi, '')
      .replace(/Red Wolves/gi, '')
      .replace(/Golden Eagles/gi, '')
      .replace(/Golden Bears/gi, '')
      .replace(/Wolf Pack/gi, '')
      .replace(/Fighting Irish/gi, '')
      .replace(/Yellow Jackets/gi, '')
      .replace(/Demon Deacons/gi, '')
      .replace(/Horned Frogs/gi, '')
      .replace(/Red Raiders/gi, '')
      .trim();
    nicknames.forEach(nick => {
      result = result.replace(new RegExp('\\s+' + nick.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$', 'i'), '').trim();
    });
    return result || teamName;
  };
const DailyDegen = ({ mlbGameContext, nbaTeamData, gamesData, fanmatchData, parlayLegs, setParlayLegs, setActiveTab, setMybetsTab, showToast, ANTHROPIC_API_KEY, supabase, isPlayoffMode, playoffSeries }) => {
  const [degenData, setDegenData] = React.useState(null);
  const [degenLoading, setDegenLoading] = React.useState(false);
  const [degenError, setDegenError] = React.useState('');

  React.useEffect(() => { fetchDailyDegen(); }, []);

  const fetchDailyDegen = async () => {
    const _now = new Date();
    const today = _now.getFullYear() + '-' + String(_now.getMonth()+1).padStart(2,'0') + '-' + String(_now.getDate()).padStart(2,'0');
    setDegenLoading(true);

    // Server-side generation: read the one canonical record for today.
    // Pipeline generates this once (afternoon cron) — all users read identical data.
    try {
      const { data: row, error } = await supabase
        .from('daily_degen')
        .select('*')
        .eq('game_date', today)
        .single();
      if (row && row.legs && row.legs.length >= 2) {
        setDegenData({
          legs: row.legs,
          narrative: row.narrative || '',
          leg_count: row.leg_count,
          avg_conviction: row.avg_conviction,
          generatedAt: today,
        });
        setDegenLoading(false);
        return;
      }
    } catch (e) {}

    // No server-generated Degen yet today — show empty state
    setDegenData({ noPlays: true });
    setDegenLoading(false);
    return;

    // --- Legacy client-side generation retained below (unreachable) for reference ---
    /* eslint-disable */
    // @ts-nocheck-begin
    const _unreachable = async () => {

    try {
      const legs = [];

      // 1. Scan MLB for NRFI plays — only 88-94 sweet spot (77% hit rate)
const seen = new Set();
const mlbCtxValues = Object.values(mlbGameContext).filter((ctx: any) => {
  if(!ctx.game_id || seen.has(ctx.game_id)) return false;
  seen.add(ctx.game_id);
  const game = gamesData.find((g: any) =>
    g.home_team === ctx.home_team || g.away_team === ctx.away_team
  );
  if(game) {
    const gameTime = new Date(game.commence_time);
    if(gameTime <= new Date()) return false;
  }
  return true;
});
      const topNRFI = mlbCtxValues
        .filter((ctx: any) => ctx.nrfi_score >= 88 && ctx.nrfi_score <= 94 && ctx.game_date === today)
        .sort((a: any, b: any) => b.nrfi_score - a.nrfi_score)
        .slice(0, 2);

      for(const ctx of topNRFI) {
        const game = gamesData.find((g: any) =>
          g.home_team === ctx.home_team || g.away_team === ctx.away_team
        );
        const odds = game?.bookmakers?.[0]?.markets?.find((m: any) => m.key === 'h2h')?.outcomes?.[0]?.price || -110;
        legs.push({
          type: 'NRFI',
          matchup: `${ctx.away_team} @ ${ctx.home_team}`,
          pick: `NRFI`,
          odds: -120,
          signal: `NRFI Score ${ctx.nrfi_score} — ${ctx.home_pitcher} xERA ${ctx.home_sp_xera} + ${ctx.away_pitcher} xERA ${ctx.away_sp_xera}`,
          game,
          ctx,
        });
      }

      // 2. Scan MLB totals — over leans only (55.9% hit rate), unders excluded (46.2%)
      const topTotals = mlbCtxValues
        .filter((ctx: any) => ctx.over_lean === true && ctx.projected_total && ctx.game_date === today)
        .map((ctx: any) => {
          const game = gamesData.find((g: any) => g.home_team === ctx.home_team);
          if(!game) return null;
          const totalMkt = game?.bookmakers?.[0]?.markets?.find((m: any) => m.key === 'totals');
          const totalLine = totalMkt?.outcomes?.[0]?.point;
          if(!totalLine) return null;
          const delta = ctx.projected_total - totalLine;
          if(delta < 0.5) return null;
          return { ctx, game, totalLine, delta, isOver: true };
        })
        .filter(Boolean)
        .sort((a, b) => b.delta - a.delta)
        .slice(0, 1);

      for(const item of topTotals) {
        const odds = item.game?.bookmakers?.[0]?.markets?.find((m: any) => m.key === 'totals')?.outcomes?.find((o: any) => o.name === 'Over')?.price || -110;
        legs.push({
          type: 'OVER',
          matchup: `${item.ctx.away_team} @ ${item.ctx.home_team}`,
          pick: `Over ${item.totalLine}`,
          odds,
          signal: `Model projects ${item.ctx.projected_total} runs — ${item.delta.toFixed(1)} run gap vs market (55.9% over lean hit rate)`,
          game: item.game,
          ctx: item.ctx,
        });
      }

      // 3. Scan MLB moneyline — spread delta 3+ (60% win rate proven)
      const mlEdges = mlbCtxValues
        .filter((ctx: any) => ctx.spread_delta != null && ctx.game_date === today)
        .map((ctx: any) => {
          const delta = parseFloat(ctx.spread_delta);
          if(Math.abs(delta) < 3.0) return null;
          const game = gamesData.find((g: any) => g.home_team === ctx.home_team);
          if(!game) return null;
          const favTeam = delta > 0 ? ctx.home_team : ctx.away_team;
          const mlMkt = game?.bookmakers?.[0]?.markets?.find((m: any) => m.key === 'h2h');
          const mlOdds = mlMkt?.outcomes?.find((o: any) => o.name === favTeam)?.price;
          if(!mlOdds || mlOdds < -200) return null;
          return { ctx, game, delta, favTeam, mlOdds };
        })
        .filter(Boolean)
        .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
        .slice(0, 1);

      for(const item of mlEdges) {
        if(legs.some(l => l.matchup === `${item.ctx.away_team} @ ${item.ctx.home_team}`)) continue;
        legs.push({
          type: 'MLB',
          matchup: `${item.ctx.away_team} @ ${item.ctx.home_team}`,
          pick: `${item.favTeam} ML`,
          odds: item.mlOdds,
          signal: `Spread delta ${item.delta > 0 ? '+' : ''}${item.delta.toFixed(1)} runs vs market — high-conviction ML lean`,
          game: item.game,
          ctx: item.ctx,
        });
      }

      // 4. Scan NBA for strong spread leans (up to 2 legs)
      const nbaGames = gamesData.filter((g: any) => g.sport_key === 'basketball_nba' || g.sport_title === 'NBA');
      let nbaLegsAdded = 0;
      for(const game of nbaGames.slice(0, 8)) {
        if(nbaLegsAdded >= 2) break;
        const homeNBA = Object.values(nbaTeamData).find((t: any) => t.team && game.home_team.includes(t.team.split(' ').pop()));
        const awayNBA = Object.values(nbaTeamData).find((t: any) => t.team && game.away_team.includes(t.team.split(' ').pop()));
        if(!homeNBA || !awayNBA) continue;
        const netGap = Math.abs((homeNBA as any).net_rating - (awayNBA as any).net_rating);
        if(netGap >= 4) {
          const favTeam = (homeNBA as any).net_rating > (awayNBA as any).net_rating ? game.home_team : game.away_team;
          const spreadMkt = game?.bookmakers?.[0]?.markets?.find((m: any) => m.key === 'spreads');
          const favSpread = spreadMkt?.outcomes?.find((o: any) => o.name === favTeam);
          if(favSpread) {
            legs.push({
              type: 'NBA',
              matchup: `${game.away_team} @ ${game.home_team}`,
              pick: `${favTeam} ${favSpread.point > 0 ? '+' : ''}${favSpread.point}`,
              odds: favSpread.price,
              signal: `Net rating gap ${netGap.toFixed(1)} pts — model favors ${favTeam.split(' ').pop()}`,
              game,
            });
            nbaLegsAdded++;
          }
        }
      }

      // 5. Scan NBA totals using projected total model
      for(const game of nbaGames.slice(0, 8)) {
        const homeNBA = Object.values(nbaTeamData).find((t: any) => t.team && game.home_team.includes(t.team.split(' ').pop())) as any;
        const awayNBA = Object.values(nbaTeamData).find((t: any) => t.team && game.away_team.includes(t.team.split(' ').pop())) as any;
        if(!homeNBA || !awayNBA || !homeNBA.pace || !homeNBA.offensive_rating) continue;
        // Already has a side leg from this game? skip to avoid correlation
        if(legs.some(l => l.matchup === `${game.away_team} @ ${game.home_team}`)) continue;
        const projPoss = (homeNBA.pace + awayNBA.pace) / 2 - 3;
        const homeExp = projPoss * ((homeNBA.offensive_rating + awayNBA.defensive_rating) / 2) / 100;
        const awayExp = projPoss * ((awayNBA.offensive_rating + homeNBA.defensive_rating) / 2) / 100;
        let projTotal = homeExp + awayExp;
        // eFG adjustment
        const homeOppEFG = parseFloat(homeNBA.opp_efg_pct) || 0;
        const awayOppEFG = parseFloat(awayNBA.opp_efg_pct) || 0;
        if(homeOppEFG > 0 && awayOppEFG > 0) {
          projTotal += ((homeNBA.efg_pct - awayOppEFG) + (awayNBA.efg_pct - homeOppEFG)) * 0.8;
        }
        // Injury adjustment
        if(homeNBA.injury_note?.includes('OUT')) projTotal -= 3;
        if(awayNBA.injury_note?.includes('OUT')) projTotal -= 3;
        const totalMkt = game?.bookmakers?.[0]?.markets?.find((m: any) => m.key === 'totals');
        const postedLine = totalMkt?.outcomes?.[0]?.point;
        if(!postedLine) continue;
        const delta = projTotal - postedLine;
        if(Math.abs(delta) >= 4) {
          const side = delta > 0 ? 'Over' : 'Under';
          const totalOdds = totalMkt?.outcomes?.find((o: any) => o.name === side)?.price || -110;
          legs.push({
            type: 'NBA',
            matchup: `${game.away_team} @ ${game.home_team}`,
            pick: `${side} ${postedLine}`,
            odds: totalOdds,
            signal: `Model projects ${projTotal.toFixed(1)} pts — ${Math.abs(delta).toFixed(1)} pt gap vs posted ${postedLine}`,
            game,
          });
          break; // max 1 NBA total leg
        }
      }

      // Validate legs against model signals — drop legs that conflict
      const validatedLegs = legs.filter(leg => {
        if(leg.type === 'NRFI') return true;
        if(leg.type === 'UNDER' && leg.ctx) {
          if(leg.ctx.over_lean === true) return false;
          return true;
        }
        if(leg.type === 'OVER' && leg.ctx) {
          if(leg.ctx.over_lean === false) return false;
          return true;
        }
        if(leg.type === 'MLB') return true; // pitcher edge already validated
        if(leg.type === 'NBA' && leg.game) {
          const homeNBA = Object.values(nbaTeamData).find((t: any) => t.team && leg.game.home_team.includes(t.team.split(' ').pop()));
          const awayNBA = Object.values(nbaTeamData).find((t: any) => t.team && leg.game.away_team.includes(t.team.split(' ').pop()));
          if(homeNBA && awayNBA) {
            const netGap = Math.abs((homeNBA as any).net_rating - (awayNBA as any).net_rating);
            if(netGap < 3) return false;
            const betterTeam = (homeNBA as any).net_rating > (awayNBA as any).net_rating ? leg.game.home_team : leg.game.away_team;
            if(!leg.pick.includes(betterTeam.split(' ').pop())) return false;
          }
        }
        return true;
      });

      // Limit to 3-4 legs, pick best non-correlated
      const finalLegs = validatedLegs.slice(0, 4);

      if(finalLegs.length < 2) {
        setDegenData({ noPlays: true });
        setDegenLoading(false);
        return;
      }

      // Generate Jerry narrative
      const legsDesc = finalLegs.map((l, i) => `Leg ${i+1}: ${l.pick} (${l.matchup}) — ${l.signal}`).join('\n');
      const aiResp = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': ANTHROPIC_API_KEY,
          'anthropic-version': '2023-06-01',
        },
        body: JSON.stringify({
          model: 'claude-haiku-4-5-20251001',
          max_tokens: 200,
          messages: [{
            role: 'user',
            content: `You are Jerry — sharp, energetic, slightly degenerate but always analytically grounded. Build a narrative for today's Degen Parlay.

Legs:
${legsDesc}

Write 2-3 sentences MAX. Reference the specific data signals. Sound like a sharp friend who found edges today. End with something like "Jerry's riding all of these." or "That's the Degen Parlay." Never say "bet" or "must play". High energy but data-backed.`
          }]
        })
      });
      const aiData = await aiResp.json();
      const narrative = aiData?.content?.[0]?.text || "Model found edges across the slate today. Jerry's playing all of these.";

      const result = { legs: finalLegs, narrative, generatedAt: today };

      // Cache in Supabase
      try {
        await supabase.from('jerry_cache').upsert({
          cache_key: `daily_degen_${today}`,
          data: result,
          fetched_at: new Date().toISOString(),
        }, { onConflict: 'cache_key' });
      } catch(e) {}

      setDegenData(result);
    } catch(e) {
      setDegenData({ noPlays: true });
    }
    setDegenLoading(false);
    };
    // @ts-nocheck-end
    /* eslint-enable */
  };

  const addAllToParlay = () => {
    if(!degenData?.legs) return;
    let added = 0;
    degenData.legs.forEach((leg: any) => {
      const isNeg = leg.odds < 0;
      const newLeg = {
        id: Date.now() + Math.random(),
        matchup: leg.matchup,
        pick: leg.pick,
        odds: String(Math.abs(leg.odds)),
        oddsSign: isNeg ? '-' : '+'
      };
      setParlayLegs((prev: any) => {
        if(prev.some((l: any) => l.matchup === newLeg.matchup && l.pick === newLeg.pick)) return prev;
        added++;
        return [...prev, newLeg];
      });
    });
    showToast(`✅ ${degenData.legs.length} legs added to parlay`);
    setActiveTab('mybets');
    setMybetsTab('parlay_sub');
  };

  if(degenLoading) return (
    <View style={{alignItems:'center',paddingTop:60,gap:12}}>
      <Text style={{fontSize:32}}>🎲</Text>
      <Text style={{color:'#7a92a8',fontSize:14}}>Jerry is scanning the slate...</Text>
    </View>
  );

  if(!degenData || degenData.noPlays) return (
    <View style={{alignItems:'center',paddingTop:60,paddingHorizontal:40}}>
      <Text style={{fontSize:32}}>🎲</Text>
      <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:18,marginTop:16}}>No Degen Plays Today</Text>
      <Text style={{color:'#7a92a8',marginTop:8,fontSize:14,textAlign:'center'}}>Jerry didn't find enough edges for a parlay today. Check back after the 2pm pipeline update.</Text>
    </View>
  );

  return (
    <View style={{padding:16}}>
      {/* Header */}
      <View style={{backgroundColor:'rgba(255,77,109,0.08)',borderRadius:14,padding:14,marginBottom:16,borderWidth:1,borderColor:'rgba(255,77,109,0.25)'}}>
        <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
          <Text style={{color:'#ff4d6d',fontWeight:'800',fontSize:16}}>🎲 DAILY DEGEN</Text>
          <View style={{backgroundColor:'rgba(255,77,109,0.15)',borderRadius:10,paddingHorizontal:8,paddingVertical:3}}>
            <Text style={{color:'#ff4d6d',fontSize:11,fontWeight:'800'}}>{degenData.legs?.length}-LEG PARLAY</Text>
          </View>
        </View>
        <Text style={{color:'#c8d8e8',fontSize:13,lineHeight:20,fontStyle:'italic'}}>"{degenData.narrative?.replace(/#{1,6}\s/g, '').replace(/\*\*/g, '').replace(/\*/g, '').trim()}"</Text>
      </View>

      {/* Legs */}
      {degenData.legs?.map((leg: any, i: number) => (
        <View key={i} style={{backgroundColor:'#111820',borderRadius:12,padding:12,marginBottom:10,borderWidth:1,borderColor:'#1f2d3d'}}>
          <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:6}}>
            <View style={{flexDirection:'row',alignItems:'center',gap:6}}>
              <View style={{backgroundColor:leg.type==='NRFI'?'rgba(0,229,160,0.15)':leg.type==='NBA'?'rgba(0,153,255,0.15)':'rgba(255,184,0,0.15)',borderRadius:6,paddingHorizontal:6,paddingVertical:2}}>
                <Text style={{color:leg.type==='NRFI'?'#00e5a0':leg.type==='NBA'?'#0099ff':'#FFB800',fontSize:10,fontWeight:'800'}}>{leg.type}</Text>
              </View>
              <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13}}>{leg.pick}</Text>
            </View>
            <Text style={{color:leg.odds<0?'#e8f0f8':'#00e5a0',fontWeight:'800',fontSize:13}}>{leg.odds>0?'+':''}{leg.odds}</Text>
          </View>
          <Text style={{color:'#7a92a8',fontSize:11}}>{leg.matchup}</Text>
          <Text style={{color:'#4a6070',fontSize:10,marginTop:3}}>{leg.signal}</Text>
        </View>
      ))}

      {/* Add all to parlay */}
      <TouchableOpacity
        style={{backgroundColor:'#ff4d6d',borderRadius:12,padding:14,alignItems:'center',marginTop:4}}
        onPress={addAllToParlay}
      >
        <Text style={{color:'#fff',fontWeight:'800',fontSize:15}}>🎰 Add All to Parlay Builder</Text>
      </TouchableOpacity>

      <Text style={{color:'#4a6070',fontSize:11,textAlign:'center',marginTop:12}}>Updated twice daily • 8am + 2pm ET</Text>
      <Text style={{color:'#4a6070',fontSize:10,textAlign:'center',marginTop:6,paddingHorizontal:20,lineHeight:14}}>Daily Degen uses model signals only. Jerry's full game read may weigh additional situational factors differently.</Text>
    </View>
  );
};
const FadesScanner = ({ gamesData, mlbGameContext, nbaTeamData, nbaInjuryData, gamesSport, ANTHROPIC_API_KEY, supabase }) => {
  const [fadesData, setFadesData] = React.useState(null);
  const [fadesLoading, setFadesLoading] = React.useState(false);

  React.useEffect(() => { fetchFades(); }, []);

  const fetchFades = async () => {
    const _now = new Date();
    const today = _now.getFullYear() + '-' + String(_now.getMonth()+1).padStart(2,'0') + '-' + String(_now.getDate()).padStart(2,'0');

    // Check cache
    try {
      const { data: cached } = await supabase
        .from('jerry_cache')
        .select('data, fetched_at')
        .eq('cache_key', `fades_${today}`)
        .single();
      if(cached) {
        const ageHrs = (Date.now() - new Date(cached.fetched_at).getTime()) / 3600000;
        if(ageHrs < 8) {
          setFadesData(cached.data);
          return;
        }
      }
    } catch(e) {}

    setFadesLoading(true);
    try {
      const now = new Date();
      const fades: any[] = [];
      const futureGames = gamesData.filter((g:any) => new Date(g.commence_time) > now);

      for(const game of futureGames.slice(0, 25)) {
        const reasons: string[] = [];
        const sport = game.sport_key?.includes('nba') ? 'NBA' : game.sport_key?.includes('mlb') ? 'MLB' : game.sport_key?.includes('nhl') ? 'NHL' : 'OTHER';

        // ── MLB PIPELINE-DRIVEN FADES ──
        const mlbCtx = Object.values(mlbGameContext as Record<string, any>).find((ctx:any) =>
          ctx.home_team === game.home_team || ctx.away_team === game.away_team
        );
        if(mlbCtx) {
          // NRFI 95+ — historically volatile (38.5% hit rate)
          const nrfi = mlbCtx.nrfi_score;
          if(nrfi >= 95) {
            reasons.push(`NRFI score ${nrfi} looks elite but 95+ scores hit just 38% historically — model says step aside on NRFI`);
          }

          // Both pitchers xERA sanitized or missing
          if(!mlbCtx.home_sp_xera && !mlbCtx.away_sp_xera) {
            reasons.push('No pitcher xERA data — model is flying blind, unpredictable game');
          }

          // Spread delta < 1.0 — no edge
          const sDelta = mlbCtx.spread_delta != null ? Math.abs(parseFloat(mlbCtx.spread_delta)) : null;
          if(sDelta !== null && sDelta < 1.0 && mlbCtx.projected_total) {
            reasons.push(`Spread delta ${sDelta.toFixed(1)} runs — market and model agree, no ML edge`);
          }

          // Both openers / bullpen day
          if(mlbCtx.home_pitcher && mlbCtx.away_pitcher) {
            const homeLP = mlbCtx.home_last_pitch_count;
            const awayLP = mlbCtx.away_last_pitch_count;
            if(homeLP && awayLP && homeLP < 40 && awayLP < 40) {
              reasons.push('Both starters had short outings — possible bullpen games, high volatility');
            }
          }

          // Both teams weak offense — boring game, no side lean
          const hWrc = mlbCtx.home_wrc_plus ? parseFloat(mlbCtx.home_wrc_plus) : null;
          const aWrc = mlbCtx.away_wrc_plus ? parseFloat(mlbCtx.away_wrc_plus) : null;
          if(hWrc && aWrc && hWrc < 90 && aWrc < 90) {
            reasons.push(`Both offenses weak (wRC+ ${hWrc} vs ${aWrc}) — low-scoring grind with no clear lean`);
          }

          // No pitcher confirmed
          if(!mlbCtx.home_pitcher || !mlbCtx.away_pitcher) {
            reasons.push('Pitcher TBD — can\'t model the game without knowing who\'s on the mound');
          }
        }

        // ── NBA PIPELINE-DRIVEN FADES ──
        if(game.sport_key?.includes('nba') && nbaTeamData) {
          const homeNBA = Object.values(nbaTeamData as Record<string, any>).find((t:any) => t.team && game.home_team?.includes(t.team.split(' ').pop()));
          const awayNBA = Object.values(nbaTeamData as Record<string, any>).find((t:any) => t.team && game.away_team?.includes(t.team.split(' ').pop()));

          if(homeNBA && awayNBA) {
            const netGap = Math.abs((homeNBA.net_rating || 0) - (awayNBA.net_rating || 0));
            const homeDef = homeNBA.defensive_rating || 112;
            const awayDef = awayNBA.defensive_rating || 112;

            // Net rating gap < 2 — coin flip
            if(netGap < 2.0) {
              reasons.push(`Net rating gap only ${netGap.toFixed(1)} pts — too close for the model to pick a side`);
            }

            // Both middling defenses — no total lean
            if(homeDef >= 110 && homeDef <= 114 && awayDef >= 110 && awayDef <= 114) {
              reasons.push(`Both teams average defensively (${homeDef.toFixed(0)} / ${awayDef.toFixed(0)} DRtg) — no clear over/under edge`);
            }

            // Star player questionable
            const homeInj = nbaInjuryData?.[game.home_team] || [];
            const awayInj = nbaInjuryData?.[game.away_team] || [];
            const questionable = [...homeInj, ...awayInj].filter((i:any) => i.status === 'Questionable');
            if(questionable.length >= 2) {
              const names = questionable.slice(0, 3).map((i:any) => i.player_name).join(', ');
              reasons.push(`${questionable.length} questionable players (${names}) — too much lineup risk`);
            }

            // Road favorite — historically underperforms
            if(awayNBA.net_rating > homeNBA.net_rating) {
              try {
                const aw = parseInt(awayNBA.away_record?.split('-')[0] || '0');
                const al = parseInt(awayNBA.away_record?.split('-')[1] || '0');
                const awayRoadPct = aw / (aw + al || 1);
                if(awayRoadPct < 0.45) {
                  reasons.push(`${game.away_team.split(' ').pop()} favored on the road but only ${awayNBA.away_record} away — road favorites underperform`);
                }
              } catch(e) {}
            }
          }
        }

        // ── MARKET-BASED FADES (all sports) ──
        const bookmakers = game.bookmakers || [];
        const mls = bookmakers.map((bm:any) => {
          const m = bm.markets?.find((mk:any) => mk.key === 'h2h');
          return m?.outcomes?.map((o:any) => o.price) || [];
        }).flat().filter(Boolean);
        const heavyFav = mls.some((ml:number) => ml < -280);
        if(heavyFav) {
          reasons.push('Massive favorite juice (-280+) — value is gone, public has already hammered this');
        }

        if(reasons.length >= 1) {
          fades.push({
            game: `${game.away_team?.split(' ').pop()} @ ${game.home_team?.split(' ').pop()}`,
            away: game.away_team,
            home: game.home_team,
            sport: mlbCtx ? 'MLB' : game.sport_key?.includes('nba') ? 'NBA' : game.sport_key?.includes('nhl') ? 'NHL' : 'OTHER',
            commence_time: game.commence_time,
            reasons,
            reasonCount: reasons.length,
          });
        }
      }

      fades.sort((a:any, b:any) => b.reasonCount - a.reasonCount);
      const topFades = fades.slice(0, 5);

      for(const fade of topFades) {
        try {
          const aiResp = await fetch('https://api.anthropic.com/v1/messages', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'x-api-key': ANTHROPIC_API_KEY,
              'anthropic-version': '2023-06-01',
            },
            body: JSON.stringify({
              model: 'claude-haiku-4-5-20251001',
              max_tokens: 100,
              messages: [{
                role: 'user',
                content: `You are Jerry, a sharp sports analyst. This ${fade.sport} game is a FADE — a play to step aside on.\nGame: ${fade.game}\nReasons: ${fade.reasons.join('. ')}\nWrite ONE sentence explaining why there's no edge here. Be specific — reference the actual data point (xERA, wRC+, net rating, injury). Sound like a sharp bettor, not a disclaimer. Never say 'bet' or 'avoid' or 'I recommend'.`
              }]
            })
          });
          const aiData = await aiResp.json();
          fade.jerry = aiData?.content?.[0]?.text || fade.reasons[0];
        } catch(e) {
          fade.jerry = fade.reasons[0];
        }
      }

      const result = { fades: topFades, generatedAt: today };

      try {
        await supabase.from('jerry_cache').upsert({
          cache_key: `fades_${today}`,
          data: result,
          fetched_at: new Date().toISOString(),
        }, { onConflict: 'cache_key' });
      } catch(e) {}

      setFadesData(result);
    } catch(e) {
      setFadesData({ fades: [] });
    }
    setFadesLoading(false);
  };

  if(fadesLoading) return (
    <View style={{alignItems:'center',paddingTop:60,gap:12}}>
      <Text style={{fontSize:32}}>🚫</Text>
      <Text style={{color:'#7a92a8',fontSize:14}}>Jerry is scanning for traps...</Text>
    </View>
  );

  if(!fadesData || !fadesData.fades || fadesData.fades.length === 0) return (
    <View style={{alignItems:'center',paddingTop:60,paddingHorizontal:40}}>
      <Text style={{fontSize:32}}>✅</Text>
      <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:18,marginTop:16}}>Slate Looks Clean</Text>
      <Text style={{color:'#7a92a8',marginTop:8,fontSize:14,textAlign:'center'}}>No obvious traps today — Jerry doesn't see anything to fade. Green light on the board.</Text>
    </View>
  );

  return (
    <View style={{padding:16}}>
      <View style={{backgroundColor:'rgba(255,140,0,0.08)',borderRadius:14,padding:14,marginBottom:16,borderWidth:1,borderColor:'rgba(255,140,0,0.25)'}}>
        <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
          <Text style={{color:'#ff8c00',fontWeight:'800',fontSize:16}}>🚫 JERRY'S FADES</Text>
          <View style={{backgroundColor:'rgba(255,140,0,0.15)',borderRadius:10,paddingHorizontal:8,paddingVertical:3}}>
            <Text style={{color:'#ff8c00',fontSize:11,fontWeight:'800'}}>{fadesData.fades.length} FADES</Text>
          </View>
        </View>
        <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Games where the model sees no edge. Missing data, volatile matchups, or market consensus — sometimes the best play is no play.</Text>
      </View>

      {fadesData.fades.map((fade, i) => {
        const gameTime = new Date(fade.commence_time);
        return (
          <View key={i} style={{backgroundColor:'#111820',borderRadius:12,padding:12,marginBottom:10,borderWidth:1,borderColor:'rgba(255,140,0,0.3)',borderLeftWidth:3,borderLeftColor:'#ff8c00'}}>
            <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:6}}>
              <View style={{flex:1}}>
                <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14}}>{fade.game}</Text>
                <Text style={{color:'#4a6070',fontSize:11,marginTop:2}}>{fade.sport} • {gameTime.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true})} ET</Text>
              </View>
              <View style={{backgroundColor:'rgba(255,140,0,0.15)',borderRadius:8,paddingHorizontal:8,paddingVertical:4,borderWidth:1,borderColor:'rgba(255,140,0,0.4)'}}>
                <Text style={{color:'#ff8c00',fontWeight:'800',fontSize:11}}>🚫 FADE</Text>
              </View>
            </View>
            {fade.reasons.map((reason, j) => (
              <View key={j} style={{flexDirection:'row',alignItems:'center',gap:6,marginBottom:4}}>
                <Text style={{color:'#ff8c00',fontSize:10}}>⚠️</Text>
                <Text style={{color:'#7a92a8',fontSize:11}}>{reason}</Text>
              </View>
            ))}
            <View style={{backgroundColor:'rgba(255,140,0,0.05)',borderRadius:8,padding:8,marginTop:6,borderLeftWidth:2,borderLeftColor:'#ff8c00'}}>
              <Text style={{color:'#7a92a8',fontSize:12,fontStyle:'italic',lineHeight:18}}>🎤 {fade.jerry}</Text>
            </View>
          </View>
        );
      })}

      <Text style={{color:'#4a6070',fontSize:10,textAlign:'center',marginTop:12}}>Fades update when games load • Jerry sees what the public doesn't</Text>
    </View>
  );
};

  export default function App() {
  const [activeTab, setActiveTab] = useState('home');
  const [mybetsTab, setMybetsTab] = useState('picks');
  const [onboardingDone, setOnboardingDone] = useState(true);
  const [onboardingStep, setOnboardingStep] = useState(0);
  const [modalVisible, setModalVisible] = useState(false);
  const [editModalVisible, setEditModalVisible] = useState(false);
  const [editingBet, setEditingBet] = useState(null);
  const [oddsData, setOddsData] = useState([]);
  const [oddsLoading, setOddsLoading] = useState(false);
  const [oddsSport, setOddsSport] = useState('NBA');
  const [refreshing, setRefreshing] = useState(false);
  const [betsLoaded, setBetsLoaded] = useState(false);
  const [bets, setBets] = useState([]);
  const [propsSport, setPropsSport] = useState('NBA');
  const [propsData, setPropsData] = useState([]);
  const [propsSearch, setPropsSearch] = useState('');
const [playersSearch, setPlayersSearch] = useState('');
  const [propsLoading, setPropsLoading] = useState(false);
  const [playerStats, setPlayerStats] = useState([]);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsTab, setStatsTab] = useState('props');
  const [parlayLegs, setParlayLegs] = useState([]);
  const [parlayWager, setParlayWager] = useState('10');
  const [addLegModal, setAddLegModal] = useState(false);
  const [legForm, setLegForm] = useState({matchup:'',pick:'',odds:'',oddsSign:'-'});
  const [gamesDay, setGamesDay] = useState('today');
  const [gamesSport, setGamesSport] = useState('NBA');
  const [gamesData, setGamesData] = useState([]);
  const [gamesLoading, setGamesLoading] = useState(false);
  const [selectedGame, setSelectedGame] = useState(null);
  const [gameNarrative, setGameNarrative] = useState('');
  const [gameNarrativeLoading, setGameNarrativeLoading] = useState(false);
  const [dailyBriefing, setDailyBriefing] = useState('');
  const [dailyBriefingLoading, setDailyBriefingLoading] = useState(false);
  const [dailyBestBet, setDailyBestBet] = useState(null);
const [dailyBestBetLoading, setDailyBestBetLoading] = useState(false);
const [bestBetFetched, setBestBetFetched] = useState(false);
const [dailyBestBetError, setDailyBestBetError] = useState('');
const [modelEdgeData, setModelEdgeData] = useState([]);
const [mlbGameContext, setMlbGameContext] = useState({});
const [modelEdgeLoading, setModelEdgeLoading] = useState(false);
  const [gameDetailModal, setGameDetailModal] = useState(false);
  const [pickRecap, setPickRecap] = useState('');
  const [pickRecapVisible, setPickRecapVisible] = useState(false);
  const [parlayAnalysis, setParlayAnalysis] = useState('');
  const [parlayAnalysisLoading, setParlayAnalysisLoading] = useState(false);
  const [parlayAnalysisVisible, setParlayAnalysisVisible] = useState(false);
  const [matchupTab, setMatchupTab] = useState('money');
  const [scheduleTeam, setScheduleTeam] = useState('away');
  const [scheduleGames, setScheduleGames] = useState([]);
  const [scheduleGamesLoading, setScheduleGamesLoading] = useState(false);
  const [sitMarket, setSitMarket] = useState('spread');
  const [statView, setStatView] = useState('offense');
  const [trackingMode, setTrackingMode] = useState('units');
  const [unitSize, setUnitSize] = useState('25');
  const [unitSizeModal, setUnitSizeModal] = useState(false);
  const [tempUnitSize, setTempUnitSize] = useState('25');
  const [toastMsg, setToastMsg] = useState('');
  const [toastVisible, setToastVisible] = useState(false);
  const [trendsTab, setTrendsTab] = useState('propjerry');
  const [evData, setEvData] = useState([]);
  const [evLoading, setEvLoading] = useState(false);
  const [evSport, setEvSport] = useState('NBA');
  const [modelEdgeSport, setModelEdgeSport] = useState('NCAAB');
  const [sharpData, setSharpData] = useState([]);
  const [propJerryLastUpdate, setPropJerryLastUpdate] = useState(null);
  const [sharpLoading, setSharpLoading] = useState(false);
  const [sharpSport, setSharpSport] = useState('NBA');
   const [propJerrySport, setPropJerrySport] = useState('MLB');
   const [jerryHistory, setJerryHistory] = useState([]);
  const [jerryRecord, setJerryRecord] = useState(null);
  const [jerryRecordLoading, setJerryRecordLoading] = useState(false);
  const [propJerryData, setPropJerryData] = useState([]);
  const [propJerryLoading, setPropJerryLoading] = useState(false);
  // Pipeline-driven MLB props (replaces EV scanner for MLB sport)
  const [pipelineMLBProps, setPipelineMLBProps] = useState([]);
  const [pipelineMLBLoading, setPipelineMLBLoading] = useState(false);
  const [propOfDay, setPropOfDay] = useState(null);
  const [propOfDayLoading, setPropOfDayLoading] = useState(false);
  const [hrWatch, setHrWatch] = useState<any[]>([]);
  const [hrWatchLoading, setHrWatchLoading] = useState(false);
  const [expandedPropJerry, setExpandedPropJerry] = useState(null);
  const [roiChartTab, setRoiChartTab] = useState('cumulative');
  const [roiTimeRange, setRoiTimeRange] = useState('all');
  const [roiUnit, setRoiUnit] = useState('units');
    const [sweatScores, setSweatScores] = useState({});
    const [historicalOdds, setHistoricalOdds] = useState({});
    const [altLines, setAltLines] = useState({});
const [altLinesLoading, setAltLinesLoading] = useState({});
  const [historicalOddsLoading, setHistoricalOddsLoading] = useState({});
  const [propHistoryModal, setPropHistoryModal] = useState(false);
  const [settingsModal, setSettingsModal] = useState(false);
  const [showPrivacy, setShowPrivacy] = useState(false);
  const [showTerms, setShowTerms] = useState(false);
  const [selectedPropPlayer, setSelectedPropPlayer] = useState(null);
  const [propHistoryData, setPropHistoryData] = useState([]);
  const [propHistoryLoading, setPropHistoryLoading] = useState(false);
  const [propHistoryTab, setPropHistoryTab] = useState('bars');
  const [propHistoryRange, setPropHistoryRange] = useState('last10');
  const [propHistoryStat, setPropHistoryStat] = useState('pts');
  const [expandedSweatScore, setExpandedSweatScore] = useState(null);
  const [bartData, setBartData] = useState([]);
  const [gamesSearch, setGamesSearch] = useState('');
  const [gamesSort, setGamesSort] = useState('time');
   const [fanmatchData, setFanmatchData] = useState({});
  const [nbaTeamData, setNbaTeamData] = useState({});
  const [nbaInjuryData, setNbaInjuryData] = useState<Record<string, any[]>>({});
  const [playoffSeries, setPlayoffSeries] = useState({});
  const [isPlayoffMode, setIsPlayoffMode] = useState(false);
    const [scoresCache, setScoresCache] = useState({});
  const [scoresLoading, setScoresLoading] = useState(false);
  const [form, setForm] = useState({matchup:'',pick:'',sport:'NBA',type:'Spread',odds:'',units:'',book:'Hard Rock',result:'Pending',oddsSign:'-'});
const [ageGateVisible, setAgeGateVisible] = useState(false);
  const [onboardingVisible, setOnboardingVisible] = useState(false);
  const [hasSeenOnboarding, setHasSeenOnboarding] = useState(false);
  const showToast = (msg) => {
    setToastMsg(msg); setToastVisible(true);
    setTimeout(() => setToastVisible(false), 2500);
  };

  useEffect(() => {
    const loadData = async () => {
      try {
        const onboarded = await AsyncStorage.getItem('sweatlocker_onboarded');
        if(!onboarded) setOnboardingDone(false);
        const stored = await AsyncStorage.getItem(STORAGE_KEY);
        if (stored !== null) setBets(JSON.parse(stored));
        else {
          const seed = [
            {id:1,matchup:'Lakers vs Celtics',pick:'Lakers -3.5',sport:'NBA',type:'Spread',odds:'-110',units:'2',book:'Hard Rock',result:'Win',date:new Date().toLocaleDateString('en-US',{month:'short',day:'numeric'})},
            {id:2,matchup:'Chiefs vs Ravens',pick:'Over 47.5',sport:'NFL',type:'Total (O/U)',odds:'-108',units:'1.5',book:'Hard Rock',result:'Loss',date:new Date().toLocaleDateString('en-US',{month:'short',day:'numeric'})},
            {id:3,matchup:'Duke vs UNC',pick:'Duke -4.5',sport:'NCAAB',type:'Spread',odds:'-115',units:'2',book:'Hard Rock',result:'Pending',date:new Date().toLocaleDateString('en-US',{month:'short',day:'numeric'})},
          ];
          setBets(seed);
          await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(seed));
        }
        const settings = await AsyncStorage.getItem(SETTINGS_KEY);
        if (settings) {
          const p = JSON.parse(settings);
          if (p.trackingMode) setTrackingMode(p.trackingMode);
          if (p.unitSize) setUnitSize(p.unitSize);
        }
        const jerryHist = await AsyncStorage.getItem(JERRY_HISTORY_KEY);
if(jerryHist) setJerryHistory(JSON.parse(jerryHist));
      } catch(e) { console.error(e); }
      setBetsLoaded(true);
      const seen = await AsyncStorage.getItem('hasSeenOnboarding');
        if(!seen) {
          setAgeGateVisible(true);
        }
        setHasSeenOnboarding(!!seen);
        fetchBartData();
        await AsyncStorage.removeItem('sweatlocker_fanmatch_cache'); // TEMP
        fetchKenpomFanmatch();
        fetchNBATeamData();
    };
    loadData();
    fetchDailyBriefing();
  }, []);


  useEffect(() => {
    if(betsLoaded && bets.length) autoDetectResults();

  }, []);

  useEffect(() => {
    if (!betsLoaded) return;
    AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(bets)).catch(e => console.error(e));
  }, [bets, betsLoaded]);

  const saveSettings = async (mode, size) => {
    try { await AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify({trackingMode:mode,unitSize:size})); }
    catch(e) { console.error(e); }
  };
  const toggleMode = () => {
    const newMode = trackingMode==='units' ? 'dollars' : 'units';
    setTrackingMode(newMode); saveSettings(newMode, unitSize);
    if (newMode==='dollars') setUnitSizeModal(true);
  };

  useEffect(() => {
  if(bartData.length) {
    fetchMLBGameContext();
  }
}, [bartData]);
// Best Bet waits for nbaTeamData + mlbGameContext to load first
// Play of the Day — computed server-side by pipeline, app just reads it
// No dependency on mlbGameContext or nbaTeamData loading
useEffect(() => {
  if(bestBetFetched) return;
  setBestBetFetched(true);
  fetchDailyBestBet();
}, []);
useEffect(() => {
  if(bartData.length) {
    fetchNBATeamContext();
    fetchNBAInjuries();
    fetchPlayoffSeries();
  }
}, [bartData]);
useEffect(() => {
  if(bartData.length) {
    fetchModelEdgeGames('NCAAB');
  }
}, [bartData]);
  const saveUnitSize = () => { setUnitSize(tempUnitSize); saveSettings(trackingMode,tempUnitSize); setUnitSizeModal(false); };
  const usd = parseFloat(unitSize)||25;
  const formatBetSize = (units) => trackingMode==='dollars' ? '$'+(parseFloat(units||0)*usd).toFixed(0) : units+'u';

  const wins = bets.filter(b=>b.result==='Win').length;
  const losses = bets.filter(b=>b.result==='Loss').length;
  const pushes = bets.filter(b=>b.result==='Push').length;
  const totalUnits = bets.reduce((sum,b) => {
  const units = parseFloat(b.units||0) || 1;
  const odds = parseInt(b.odds) || -110;
  if(b.result==='Win') {
    const profit = odds > 0 ? units * (odds/100) : units * (100/Math.abs(odds));
    return sum + profit;
  }
  if(b.result==='Loss') return sum - units;
  return sum;
}, 0);
  const totalDollars = totalUnits*usd;
  const winRate = wins+losses>0 ? ((wins/(wins+losses))*100).toFixed(1) : '0.0';
  const resultColor = (r) => r==='Win'?'#00e5a0':r==='Loss'?'#ff4d6d':r==='Push'?'#ffd166':'#0099ff';

  const getHRBLine = (game) => {
    if(!game||!game.bookmakers) return null;
    //console.log('getHRBLine keys:', game.bookmakers.map(bm=>bm.key));
    const hrbBm = game.bookmakers.find(bm => bm.key==='hardrockbet' || bm.key==='hardrock' || (BOOKMAKER_MAP[bm.key]||bm.key)===HRB);
   //console.log('hrbBm found:', hrbBm?.key);
    if(!hrbBm) return null;
    const spread = hrbBm.markets && hrbBm.markets.find(m => m.key==='spreads');
    const total = hrbBm.markets && hrbBm.markets.find(m => m.key==='totals');
    const ml = hrbBm.markets && hrbBm.markets.find(m => m.key==='h2h');
    return {
      spread: spread&&spread.outcomes ? spread.outcomes : null,
      total: total&&total.outcomes ? total.outcomes : null,
      ml: ml&&ml.outcomes ? ml.outcomes : null,
    };
  };

  const getHRBEV = (game) => {
    if(!game||!game.bookmakers) return null;
    const results = [];
    ['spreads','totals','h2h'].forEach(market => {
      const allOutcomes = {};
      game.bookmakers.forEach(bm => {
        const mkt = bm.markets && bm.markets.find(m => m.key===market);
        if(!mkt) return;
        mkt.outcomes.forEach(outcome => {
          const key = outcome.name+(outcome.point!==undefined?'_'+outcome.point:'');
          if(!allOutcomes[key]) allOutcomes[key]={name:outcome.name,point:outcome.point,bookOdds:[]};
          allOutcomes[key].bookOdds.push({book:BOOKMAKER_MAP[bm.key]||bm.key,odds:outcome.price});
        });
      });
      Object.values(allOutcomes).forEach(outcome => {
        if(outcome.bookOdds.length<2) return;
        const probs = outcome.bookOdds.map(b => impliedProbRaw(b.odds));
        const vfProbs = vigFreeProb(probs);
        const hrbLine = outcome.bookOdds.find(b => b.book===HRB);
        if(!hrbLine) return;
        const hrbIdx = outcome.bookOdds.indexOf(hrbLine);
        const ev = calcEV(hrbLine.odds, vfProbs[hrbIdx]);
        results.push({
          pick: outcome.name+(outcome.point!==undefined?' '+(outcome.point>0?'+':'')+outcome.point:''),
          market: market==='spreads'?'Spread':market==='totals'?'Total':'ML',
          odds: hrbLine.odds, ev, isPositive: ev>0,
        });
      });
    });
    return results;
  };

  const myTrends = () => {
    const settled = bets.filter(b=>b.result==='Win'||b.result==='Loss');
    const bySport={}, byType={}, byBook={};
    settled.forEach(b => {
      if (!bySport[b.sport]) bySport[b.sport]={w:0,l:0};
      if (!byType[b.type]) byType[b.type]={w:0,l:0};
      if (!byBook[b.book]) byBook[b.book]={w:0,l:0};
      if (b.result==='Win'){bySport[b.sport].w++;byType[b.type].w++;byBook[b.book].w++;}
      else{bySport[b.sport].l++;byType[b.type].l++;byBook[b.book].l++;}
    });
    const toArr = (obj) => Object.entries(obj).map(([k,v])=>({
      label:k,w:v.w,l:v.l,pct:v.w+v.l>0?((v.w/(v.w+v.l))*100).toFixed(1):'—',total:v.w+v.l
    })).sort((a,b)=>b.total-a.total);
    let streak=0, streakType='';
    for (let i=0;i<bets.length;i++){
      const b=bets[i]; if(b.result==='Pending') continue;
      if(streakType===''){streakType=b.result;streak=1;}
      else if(b.result===streakType) streak++;
      else break;
    }
    const sportArr=toArr(bySport);
    const best=sportArr.find(s=>s.total>=2&&parseFloat(s.pct)>=50);
    const worst=sportArr.slice().reverse().find(s=>s.total>=2&&parseFloat(s.pct)<50);
    return{bySport:toArr(bySport),byType:toArr(byType),byBook:toArr(byBook),streak,streakType,best,worst,total:settled.length};
  };

  const clvBets = bets.filter(b=>b.odds&&b.result!=='Pending').map(b=>{
    const myOdds=parseFloat(b.odds);
    const closingOdds=myOdds>0?myOdds-5:myOdds+5;
    const clv=impliedProbRaw(myOdds)-impliedProbRaw(closingOdds);
    return{...b,myOdds,closingOdds,clv,beatClosing:clv<0};
  });
  const avgCLV=clvBets.length>0?(clvBets.reduce((a,b)=>a+b.clv,0)/clvBets.length).toFixed(2):0;
  const clvPositive=clvBets.filter(b=>b.beatClosing).length;

  const fetchEV = async (sport=evSport) => {
    setEvLoading(true);
    try {
      const noHistoryScore = ['soccer_epl','soccer_usa_mls','golf_masters_tournament_winner'].includes(SPORT_KEYS[sport]);
      const supported = ['NBA','NFL','NHL','MLB','NCAAB','NCAAF'];
      if(!supported.includes(sport)) return [];
      const r = await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/odds', {
  params: {
    apiKey: ODDS_API_KEY,
    regions: 'us,us2',
    markets: 'spreads,totals,h2h',
    oddsFormat: 'american',
    bookmakers: 'hardrockbet,draftkings,fanduel,espnbet,betmgm,caesars'
  }
});
      const evOpps=[];
      r.data.forEach(game=>{
        ['spreads','totals','h2h'].forEach(market=>{
          const allOutcomes={};
          game.bookmakers.forEach(bm=>{
            const mkt=bm.markets&&bm.markets.find(m=>m.key===market);
            if(!mkt)return;
            mkt.outcomes.forEach(outcome=>{
              const key=outcome.name+(outcome.point!==undefined?'_'+outcome.point:'');
              if(!allOutcomes[key]) allOutcomes[key]={name:outcome.name,point:outcome.point,market,game,bookOdds:[]};
              allOutcomes[key].bookOdds.push({book:BOOKMAKER_MAP[bm.key]||bm.key,odds:outcome.price});
            });
          });
          Object.values(allOutcomes).forEach(outcome=>{
            if(outcome.bookOdds.length<2)return;
            const probs=outcome.bookOdds.map(b=>impliedProbRaw(b.odds));
            const vfProbs=vigFreeProb(probs);
            outcome.bookOdds.forEach((b,i)=>{
              const ev=calcEV(b.odds,vfProbs[i]);
              if(ev>1.5) evOpps.push({
                game:outcome.game.away_team+' @ '+outcome.game.home_team,
                pick:outcome.name+(outcome.point!==undefined?' '+(outcome.point>0?'+':'')+outcome.point:''),
                market:market==='spreads'?'Spread':market==='totals'?'Total':'ML',
                book:b.book,odds:b.odds,ev:ev.toFixed(1),
                marketProb:vfProbs[i].toFixed(1),commenceTime:outcome.game.commence_time,
                isHRB:b.book===HRB,
              });
            });
          });
        });
      });
      // Sort: HRB first, then spreads/totals before ML, then by EV
evOpps.sort((a,b)=>{
  if(a.isHRB&&!b.isHRB) return -1;
  if(!a.isHRB&&b.isHRB) return 1;
  const aIsML = a.market==='ML';
  const bIsML = b.market==='ML';
  if(!aIsML&&bIsML) return -1;
  if(aIsML&&!bIsML) return 1;
  return parseFloat(b.ev)-parseFloat(a.ev);
});
setEvData(evOpps.slice(0,20));
    }catch(e){setEvData([]);}
    setEvLoading(false);setRefreshing(false);
  };

  const fetchSharp = async (sport=sharpSport) => {
    setSharpLoading(true);
    try {
      const r=await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/odds',{
        params:{apiKey:ODDS_API_KEY,regions:'us,us2',markets:'spreads,h2h',oddsFormat:'american',bookmakers:'hardrockbet,draftkings,fanduel,betmgm,caesars'}
      });
      const moves=r.data.map(game=>{
        const spreads=(game.bookmakers||[]).map(bm=>{
          const s=bm.markets&&bm.markets.find(m=>m.key==='spreads');
          return s&&s.outcomes&&s.outcomes[0]?s.outcomes[0].point:null;
        }).filter(x=>x!==null);
        if(spreads.length<2)return null;
        const lineMove=Math.max(...spreads)-Math.min(...spreads);
        const avgSpread=(spreads.reduce((a,b)=>a+b,0)/spreads.length).toFixed(1);
        const publicPct=Math.floor(Math.random()*40)+30;
        const sharpSide=publicPct<50?game.away_team:game.home_team;
        const isReverseLineMove=lineMove>=1&&publicPct>55;
        return{game:stripMascot(game.away_team) +' @ '+stripMascot(game.home_team),lineMove:lineMove.toFixed(1),avgSpread,isSteam:lineMove>=1,publicPct,sharpSide,isReverseLineMove,commenceTime:game.commence_time};
      }).filter(Boolean).sort((a,b)=>parseFloat(b.lineMove)-parseFloat(a.lineMove));
      setSharpData(moves.slice(0,15));
    }catch(e){setSharpData([]);}
    setSharpLoading(false);setRefreshing(false);
  };

  const fetchOdds = async (sport=oddsSport) => {
    setOddsLoading(true);
    try {
      const r=await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/odds',{
        params:{apiKey:ODDS_API_KEY,regions:'us,us2',markets:'spreads,totals,h2h',oddsFormat:'american',bookmakers:'hardrockbet,draftkings,fanduel,espnbet,betmgm,caesars,bet365'}
      });
      setOddsData(r.data);
    }catch(e){setOddsData([]);}
    setOddsLoading(false);setRefreshing(false);
  };

  const fetchGames = async (sport=gamesSport, day=gamesDay, forceRefresh=false) => {
  setGamesLoading(true);
  const CACHE_KEY = `odds_games_${sport}_${day}`;
  const CACHE_MINUTES = 60;

  if(!forceRefresh) {
  // 1. Check AsyncStorage first
  try {
    const cached = await AsyncStorage.getItem('sweatlocker_games'+'_'+sport+'_'+day);
    if(cached) {
      const parsed = JSON.parse(cached);
      const ageMin = (Date.now() - parsed.timestamp) / 60000;
      if(ageMin < CACHE_MINUTES) {
        setGamesData(parsed.data);
        setGamesLoading(false);
        setRefreshing(false);
        return;
      }
    }
  } catch(e) {}

  // 2. Check Supabase cache
  try {
    const { data: supabaseCache } = await supabase
      .from('odds_cache')
      .select('data, fetched_at')
      .eq('cache_key', CACHE_KEY)
      .single();
    if(supabaseCache) {
      const ageMin = (Date.now() - new Date(supabaseCache.fetched_at).getTime()) / 60000;
      if(ageMin < CACHE_MINUTES) {
        const mappedGames = supabaseCache.data;
        setGamesData(mappedGames);
        await AsyncStorage.setItem('sweatlocker_games'+'_'+sport+'_'+day, JSON.stringify({data:mappedGames, timestamp:Date.now()}));
        setGamesLoading(false);
        setRefreshing(false);
        return;
      }
    }
  } catch(e) {}
  } // end !forceRefresh

// 3. Fetch fresh
  try {
    let mappedGames = [];

    if(sport === 'MLB') {
      // ── MLB: use MLB Stats API for schedule + Odds API for lines ──
      // Use local device date (US user ≈ ET) for MLB schedule query
      const localNow = new Date();
      if(day === 'tomorrow') localNow.setDate(localNow.getDate() + 1);
      if(day === 'yesterday') localNow.setDate(localNow.getDate() - 1);
      const dateStr = localNow.getFullYear() + '-' + String(localNow.getMonth()+1).padStart(2,'0') + '-' + String(localNow.getDate()).padStart(2,'0');

      // Fetch schedule from MLB Stats API
      const schedResp = await axios.get('https://statsapi.mlb.com/api/v1/schedule', {
        params: { sportId: 1, date: dateStr, hydrate: 'probablePitcher,linescore' }
      });
      const mlbGames = [];
      for(const dateEntry of schedResp.data?.dates || []) {
        for(const game of dateEntry.games || []) {
          // Only include scheduled or in-progress games
          const gameState = game.status?.abstractGameState || '';
          if(gameState === 'Final') continue;
          mlbGames.push({
            mlb_game_pk: String(game.gamePk),
            home_team: game.teams?.home?.team?.name || '',
            away_team: game.teams?.away?.team?.name || '',
            commence_time: game.gameDate,
            home_pitcher: game.teams?.home?.probablePitcher?.fullName || null,
            away_pitcher: game.teams?.away?.probablePitcher?.fullName || null,
            gameState,
          });
        }
      }

      // Fetch odds from Odds API and merge
      const oddsResp = await axios.get('https://api.the-odds-api.com/v4/sports/baseball_mlb/odds', {
        params: {
          apiKey: ODDS_API_KEY,
          regions: 'us,us2',
          markets: 'spreads,totals,h2h',
          oddsFormat: 'american',
          bookmakers: 'hardrockbet,draftkings,fanduel,espnbet,betmgm,caesars,williamhill_us,bet365'
        }
      });

      // Build team name normalizer for matching
      const normalizeMLB = (name: string) => name?.toLowerCase()
        .replace('athletics', 'athletics')
        .replace('a\'s', 'athletics')
        .trim() || '';

      // Merge: match by team name similarity
      mappedGames = mlbGames.map(mlbGame => {
        const oddsGame = (oddsResp.data || []).find((og: any) => {
          const homeMatch = normalizeMLB(og.home_team).includes(mlbGame.home_team.split(' ').pop()?.toLowerCase() || '')
            || mlbGame.home_team.toLowerCase().includes(normalizeMLB(og.home_team).split(' ').pop() || '');
          const awayMatch = normalizeMLB(og.away_team).includes(mlbGame.away_team.split(' ').pop()?.toLowerCase() || '')
            || mlbGame.away_team.toLowerCase().includes(normalizeMLB(og.away_team).split(' ').pop() || '');
          return homeMatch && awayMatch;
        });
        return {
          id: oddsGame?.id || mlbGame.mlb_game_pk,
          home_team: mlbGame.home_team,
          away_team: mlbGame.away_team,
          commence_time: mlbGame.commence_time,
          sport_key: 'baseball_mlb',
          sport_title: 'MLB',
          bookmakers: oddsGame?.bookmakers || [],
          home_pitcher: mlbGame.home_pitcher,
          away_pitcher: mlbGame.away_pitcher,
          mlb_game_pk: mlbGame.mlb_game_pk,
          gameState: mlbGame.gameState,
        };
      });

    } else {
      // ── All other sports: use Odds API as before ──
      const r = await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/odds', {
        params: {
          apiKey: ODDS_API_KEY,
          regions: 'us,us2',
          markets: 'spreads,totals,h2h',
          oddsFormat: 'american',
          bookmakers: 'hardrockbet,draftkings,fanduel,espnbet,betmgm,caesars,williamhill_us,bet365'
        }
      });
      const now = new Date();
      const todayStart = new Date(now); todayStart.setHours(0,0,0,0);
      const todayEnd = new Date(now); todayEnd.setHours(23,59,59,999);
      const tomorrowStart = new Date(todayEnd); tomorrowStart.setDate(tomorrowStart.getDate()+1); tomorrowStart.setHours(0,0,0,0);
      const tomorrowEnd = new Date(tomorrowStart); tomorrowEnd.setHours(23,59,59,999);
      const yesterdayStart = new Date(todayStart); yesterdayStart.setDate(yesterdayStart.getDate()-1);
      const filtered = r.data.filter((game: any) => {
        const t = new Date(game.commence_time);
        if(day==='today') return t>=todayStart && t<=todayEnd;
        if(day==='tomorrow') return t>=tomorrowStart && t<=tomorrowEnd;
        if(day==='yesterday') return t>=yesterdayStart && t<todayStart;
        return true;
      });
      mappedGames = filtered.map((g: any) => ({
        ...g,
        away_team: sport==='NCAAB' ? stripMascot(g.away_team) : g.away_team,
        home_team: sport==='NCAAB' ? stripMascot(g.home_team) : g.home_team,
      }));
    }

    setGamesData(mappedGames);

    // Save to AsyncStorage
    try {
      await AsyncStorage.setItem('sweatlocker_games'+'_'+sport+'_'+day, JSON.stringify({data:mappedGames, timestamp:Date.now()}));
    } catch(e) {}

    // Save to Supabase cache
    try {
      await supabase.from('odds_cache').upsert({
        cache_key: CACHE_KEY,
        data: mappedGames,
        fetched_at: new Date().toISOString(),
      }, {onConflict: 'cache_key'});
    } catch(e) {}

  } catch(e) { 
    setGamesData([]);
    if(e?.response?.status === 401 || e?.response?.status === 429) {
      setGamesError('Data refreshing — check back shortly.');
    }
  }
  setGamesLoading(false);
  setRefreshing(false);
};

  const fetchProps = async (sport=propsSport) => {
    if(!PROP_MARKETS[sport]){setPropsData([]);return;}
    setPropsLoading(true);
    try {
      const gamesResp=await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/odds',{
        params:{apiKey:ODDS_API_KEY,regions:'us,us2',markets:'h2h',oddsFormat:'american'}
      });
      const allProps=[];
      for(const game of gamesResp.data.slice(0,8)){
        try{
          const pr=await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/events/'+game.id+'/odds',{
            params:{apiKey:ODDS_API_KEY,regions:'us,us2',markets:PROP_MARKETS[sport].join(','),oddsFormat:'american',bookmakers:'hardrockbet,draftkings,fanduel,betmgm'}
          });
          if(pr.data&&pr.data.bookmakers&&pr.data.bookmakers.length){
            const playerMap={};
            pr.data.bookmakers.forEach(bm=>{
              if(!bm.markets)return;
              bm.markets.forEach(mkt=>{
                if(!mkt.outcomes)return;
                mkt.outcomes.forEach(outcome=>{
                  const key=outcome.description+'_'+mkt.key;
                  if(!playerMap[key]) playerMap[key]={player:outcome.description,market:PROP_LABELS[mkt.key]||mkt.key,lines:[],gameName:stripMascot(game.away_team) +' @ '+stripMascot(game.home_team)};
                  if(outcome.name==='Over') playerMap[key].lines.push({book:BOOKMAKER_MAP[bm.key]||bm.key,line:outcome.point,odds:outcome.price});
                });
              });
            });
            allProps.push(...Object.values(playerMap));
          }
        }catch(e){}
      }
      setPropsData(allProps.slice(0,50));
    }catch(e){setPropsData([]);}
    setPropsLoading(false);
  };

  const fetchPlayerStats = async () => {
    setStatsLoading(true);
    try{
      const r=await axios.get('https://api.balldontlie.io/v1/season_averages',{
        headers:{'Authorization':BDL_API_KEY},
        params:{season:2025,player_ids:[115,140,192,434,666,369,428,132]}
      });
      setPlayerStats(r.data&&r.data.data?r.data.data:[]);
    }catch(e){setPlayerStats([]);}
    setStatsLoading(false);
  };

  const fetchBartData = async () => {
    const BART_CACHE_KEY = 'sweatlocker_bart_cache';
  const SUPABASE_KEY = 'kenpom_ratings_ff_2026';

  // 1. Check AsyncStorage first (fastest)
  try {
    const cached = await AsyncStorage.getItem(BART_CACHE_KEY);
    if(cached) {
      const parsed = JSON.parse(cached);
      const ageHours = (Date.now() - parsed.timestamp) / 3600000;
      if(ageHours < 24) {
        setBartData(parsed.data);
        return;
      }
    }
  } catch(e) {}

  // 2. Check Supabase cache (protects KenPom API limit)
  try {
    const { data: supabaseCache } = await supabase
      .from('kenpom_cache')
      .select('data, fetched_at')
      .eq('cache_key', SUPABASE_KEY)
      .single();

    if(supabaseCache) {
      const ageHours = (Date.now() - new Date(supabaseCache.fetched_at).getTime()) / 3600000;
      if(ageHours < 20) {
        const mapped = supabaseCache.data;
        setBartData(mapped);
        await AsyncStorage.setItem(BART_CACHE_KEY, JSON.stringify({data: mapped, timestamp: Date.now()}));
        console.log('BartData loaded from Supabase cache');
        return;
      }
    }
  } catch(e) { console.log('Supabase cache read error:', e.message); }

  // 3. Fetch fresh from KenPom (last resort)
  try {
    const [ratingsResp, fourFactorsResp] = await Promise.all([
      axios.get('https://kenpom.com/api.php', {
        params: {endpoint: 'ratings', y: 2026},
        headers: {Authorization: `Bearer ${KENPOM_KEY}`}
      }),
      axios.get('https://kenpom.com/api.php', {
        params: {endpoint: 'four-factors', y: 2026},
        headers: {Authorization: `Bearer ${KENPOM_KEY}`}
      }),
    ]);

    const ratingsData = Array.isArray(ratingsResp.data) ? ratingsResp.data : [];
    const ffData = Array.isArray(fourFactorsResp.data) ? fourFactorsResp.data : [];
    const ffMap = {};
    ffData.forEach(t => { ffMap[t.TeamName] = t; });

    const mapped = ratingsData.map(t => {
      const ff = ffMap[t.TeamName] || {};
      return {
        team: t.TeamName,
        adjOE: parseFloat(t.AdjOE) || 109.4,
        adjDE: parseFloat(t.AdjDE) || 109.4,
        adjEM: (parseFloat(t.AdjOE) || 0) - (parseFloat(t.AdjDE) || 0),
        adjOERank: parseInt(t.RankAdjOE) || 0,
        adjDERank: parseInt(t.RankAdjDE) || 0,
        tempo: parseFloat(t.AdjTempo) || 68.0,
        tempoRank: parseInt(t.RankAdjTempo) || 0,
        eFG_O: parseFloat(ff.eFG_Pct) || 0,
        eFG_O_rank: parseInt(ff.RankeFG_Pct) || 0,
        to_O: parseFloat(ff.TO_Pct) || 0,
        to_O_rank: parseInt(ff.RankTO_Pct) || 0,
        or_O: parseFloat(ff.OR_Pct) || 0,
        or_O_rank: parseInt(ff.RankOR_Pct) || 0,
        ftr_O: parseFloat(ff.FT_Rate) || 0,
        ftr_O_rank: parseInt(ff.RankFT_Rate) || 0,
        eFG_D: parseFloat(ff.DeFG_Pct) || 0,
        eFG_D_rank: parseInt(ff.RankDeFG_Pct) || 0,
        to_D: parseFloat(ff.DTO_Pct) || 0,
        to_D_rank: parseInt(ff.RankDTO_Pct) || 0,
        or_D: parseFloat(ff.DOR_Pct) || 0,
        or_D_rank: parseInt(ff.RankDOR_Pct) || 0,
        ftr_D: parseFloat(ff.DFT_Rate) || 0,
        ftr_D_rank: parseInt(ff.RankDFT_Rate) || 0,
        wins: parseInt(t.Wins) || 0,
        losses: parseInt(t.Losses) || 0,
        conf: t.ConfShort || '',
        seed: t.Seed || null,
        luck: parseFloat(t.Luck) || 0,
        sos: parseFloat(t.SOS) || 0,
        coach: t.Coach || '',
      };
    });

    setBartData(mapped);

    // Save to AsyncStorage
    await AsyncStorage.setItem(BART_CACHE_KEY, JSON.stringify({data: mapped, timestamp: Date.now()}));

    // Save to Supabase cache
    try {
      await supabase.from('kenpom_cache').upsert({
        cache_key: SUPABASE_KEY,
        data: mapped,
        fetched_at: new Date().toISOString(),
      }, { onConflict: 'cache_key' });
      console.log('BartData saved to Supabase cache');
    } catch(e) { console.log('Supabase cache write error:', e.message); }

  } catch(e) { console.log('KenPom fetch error:', e.message); }
};

 const FANMATCH_CACHE_KEY = 'sweatlocker_fanmatch_cache';
 const PROP_JERRY_CACHE_KEY = 'sweatlocker_jerry_cache';

const fetchKenpomFanmatch = async () => {
  await AsyncStorage.removeItem('sweatlocker_fanmatch_cache'); // TEMP
  try {
    const now = new Date();
    const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    const today = fmt(now);
const tomorrow = fmt(new Date(now.getTime() + 24*60*60*1000));
const yesterday = fmt(new Date(now - 24*60*60*1000));
    // Load cache first so UI has data immediately
    try {
      const cached = await AsyncStorage.getItem(FANMATCH_CACHE_KEY);
      if(cached) {
        const {data, timestamp} = JSON.parse(cached);
        if(data) setFanmatchData(data);
        // If cache is less than 20 mins old skip fetch
        if(Date.now() - timestamp < 20*60*1000) return;
      }
    } catch(e) {}

    let r;
    try {
  r = await axios.get('https://kenpom.com/api.php', {
    params: {endpoint:'fanmatch', d:today},
    headers: {Authorization:`Bearer ${KENPOM_KEY}`},
    timeout: 15000,
  });
  if(!Array.isArray(r.data)||r.data.length===0) throw new Error('empty');
} catch(e) {
  try {
    r = await axios.get('https://kenpom.com/api.php', {
      params: {endpoint:'fanmatch', d:tomorrow},
      headers: {Authorization:`Bearer ${KENPOM_KEY}`},
      timeout: 15000,
    });
    if(!Array.isArray(r.data)||r.data.length===0) throw new Error('empty');
  } catch(e2) {
    r = await axios.get('https://kenpom.com/api.php', {
      params: {endpoint:'fanmatch', d:yesterday},
      headers: {Authorization:`Bearer ${KENPOM_KEY}`},
      timeout: 15000,
    });
  }
}
    const games = Array.isArray(r.data) ? r.data : [];
    const mapped = {};
    games.forEach(g => {
      const key = `${g.Visitor}_${g.Home}`;
      mapped[key] = {
        visitor: g.Visitor,
        home: g.Home,
        homePred: parseFloat(g.HomePred)||null,
        visitorPred: parseFloat(g.VisitorPred)||null,
        homeWP: parseFloat(g.HomeWP)||null,
        predTempo: parseFloat(g.PredTempo)||null,
        thrillScore: parseFloat(g.ThrillScore)||null,
      };
    });
    setFanmatchData(mapped);
//console.log('FANMATCH GAMES:', Object.values(mapped).map(g => g.visitor + ' vs ' + g.home));
    // Save to cache with timestamp
    try {
      await AsyncStorage.setItem(FANMATCH_CACHE_KEY, JSON.stringify({data:mapped, timestamp:Date.now()}));
    } catch(e) {}
  } catch(e) {
  }
};

  const fetchNBATeamData = async () => {
    try {
      const cacheKey = 'sweatlocker_nba_team_stats';
      const cached = await AsyncStorage.getItem(cacheKey);
      if(cached) {
        const parsed = JSON.parse(cached);
        const ageHrs = (Date.now() - parsed.timestamp) / 3600000;
        if(ageHrs < 24) { setNbaTeamData(parsed.data); return; }
      }
      const r = await axios.get('https://api.balldontlie.io/v1/teams', {
  headers: {'Authorization': BDL_API_KEY},
  params: {per_page: 30}
});
if(r.data && r.data.data) {
  const teams = r.data.data.map(t => ({
    team: t.full_name || '',
    abbrev: t.abbreviation || '',
    adjOE: 110,
    adjDE: 110,
    tempo: 100,
    winPct: '50.0',
    ppg: 110,
    oppPpg: 110,
    games: 82,
  }));

        setNbaTeamData(teams);
        await AsyncStorage.setItem(cacheKey, JSON.stringify({data:teams, timestamp:Date.now()}));
      }
    } catch(e) {
      //console.log('NBA team data error:', e?.message);
    }
  };

  const autoDetectResults = async () => {
    try {
      const pending = bets.filter(b => b.result === 'Pending' && b.type !== 'Prop');
      if(!pending.length) return;
      const sports = [...new Set(pending.map(b => b.sport))];
      let updated = false;
      const newBets = [...bets];
      for(const sport of sports) {
        const scores = await fetchScores(sport);
        if(!scores||!scores.length) continue;
        for(const bet of pending) {
          if(bet.sport !== sport) continue;
          // Fuzzy match game from bet matchup
          const parts = bet.matchup.split('@').map(s => s.trim());
          if(parts.length < 2) continue;
          const awayTeam = parts[0].trim();
          const homeTeam = parts[1].trim();
          const matchedGame = scores.find(g => {
            const cleanAway = (g.away_team||'').toLowerCase();
            const cleanHome = (g.home_team||'').toLowerCase();
            const betAway = awayTeam.toLowerCase();
            const betHome = homeTeam.toLowerCase();
            const awayMatch = cleanAway.includes(betAway.split(' ')[0]) || betAway.includes(cleanAway.split(' ')[0]);
            const homeMatch = cleanHome.includes(betHome.split(' ')[0]) || betHome.includes(cleanHome.split(' ')[0]);
            return awayMatch && homeMatch;
          });
          if(!matchedGame||!matchedGame.scores) continue;
          const awayScore = parseFloat(matchedGame.scores.find(s => s.name === matchedGame.away_team)?.score);
          const homeScore = parseFloat(matchedGame.scores.find(s => s.name === matchedGame.home_team)?.score);
          if(isNaN(awayScore)||isNaN(homeScore)) continue;
          const pick = bet.pick.toLowerCase();
          let result = null;
          if(bet.type === 'Spread') {
            // Parse spread from pick e.g. "Lakers -5.5"
            const spreadMatch = bet.pick.match(/([+-]?\d+\.?\d*)\s*$/);
            if(!spreadMatch) continue;
            const spread = parseFloat(spreadMatch[1]);
            // Determine which team bet is on
            const pickTeam = bet.pick.replace(/[+-]?\d+\.?\d*\s*$/, '').trim().toLowerCase();
            const onAway = matchedGame.away_team.toLowerCase().includes(pickTeam.split(' ')[0]) || pickTeam.includes(matchedGame.away_team.toLowerCase().split(' ')[0]);
            const adjustedScore = onAway ? (awayScore + spread) : (homeScore + spread);
            const oppScore = onAway ? homeScore : awayScore;
            if(adjustedScore > oppScore) result = 'Win';
            else if(adjustedScore < oppScore) result = 'Loss';
            else result = 'Push';
          } else if(bet.type === 'Total') {
            const totalMatch = bet.pick.match(/(\d+\.?\d*)/);
            if(!totalMatch) continue;
            const total = parseFloat(totalMatch[1]);
            const combined = awayScore + homeScore;
            const isOver = pick.includes('over');
            if(combined === total) result = 'Push';
            else if(isOver) result = combined > total ? 'Win' : 'Loss';
            else result = combined < total ? 'Win' : 'Loss';
          } else if(bet.type === 'ML') {
            const pickTeam = bet.pick.toLowerCase();
            const onAway = matchedGame.away_team.toLowerCase().includes(pickTeam.split(' ')[0]) || pickTeam.includes(matchedGame.away_team.toLowerCase().split(' ')[0]);
            const teamWon = onAway ? awayScore > homeScore : homeScore > awayScore;
            result = teamWon ? 'Win' : 'Loss';
          }
          if(result) {
            const idx = newBets.findIndex(b => b.id === bet.id);
            if(idx !== -1) {
              Alert.alert(
                '🎯 Result Detected',
                `${bet.pick}\n\nFinal: ${matchedGame.away_team} ${awayScore} - ${homeScore} ${matchedGame.home_team}\n\nResult: ${result}`,
                [
                  {text: 'Confirm', onPress: () => {
                    setBets(prev => prev.map(b => b.id === bet.id ? {...b, result} : b));
                    fetchPickRecap(bet, result);
                    quickUpdateResult(bet.id, result);
                  }},
                  {text: 'Skip', style: 'cancel'},
                ]
              );
            }
          }
        }
      }
      if(updated) setBets(newBets);
    } catch(e) {
      //console.log('Auto detect error:', e?.message);
    }
  }; 
  const fetchScores = async (sport) => {
    if(scoresCache[sport] && scoresCache[`${sport}_time`] && (Date.now() - scoresCache[`${sport}_time`]) < 10*60*1000) return scoresCache[sport];
    try {
      const r = await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/scores', {
        params: {apiKey: ODDS_API_KEY, daysFrom: 3, dateFormat: 'iso'}
      });
      //console.log('Scores raw count:', r.data?.length);
      const completed = (r.data||[]).filter(g => g.completed);
      //console.log('Scores fetched:', sport, 'total:', r.data?.length, 'completed:', completed.length);
      setScoresCache(prev => ({...prev, [sport]: completed, [`${sport}_time`]: Date.now()}));
      return completed;
    } catch(e) {
      //console.log('Scores fetch error:', sport. e?.message);
      return [];
    }
  };

  const getTeamGamesFromScores = (scores, teamName, sport) => {
    if(!scores||!scores.length) return [];
    const clean = (s) => (s||'').toLowerCase().trim();
    const teamClean = clean(teamName);
    const teamGames = scores.filter(g => {
      return clean(g.away_team).includes(teamClean.split(' ').pop()) ||
            teamClean.includes(clean(g.away_team).split(' ').pop()) ||
            clean(g.home_team).includes(teamClean.split(' ').pop()) ||
            teamClean.includes(clean(g.home_team).split(' ').pop());
    });
    return teamGames.slice(0,10).map(g => {
      const isAway = clean(g.away_team).includes(teamClean.split(' ').pop()) ||
                    teamClean.includes(clean(g.away_team).split(' ').pop());
      const teamScore = isAway ?
        g.scores?.find(s => s.name === g.away_team)?.score :
        g.scores?.find(s => s.name === g.home_team)?.score;
      const oppScore = isAway ?
        g.scores?.find(s => s.name === g.home_team)?.score :
        g.scores?.find(s => s.name === g.away_team)?.score;
      const oppName = isAway ? g.home_team : g.away_team;
      const tScore = parseInt(teamScore||0);
      const oScore = parseInt(oppScore||0);
      const win = tScore > oScore;
      const diff = tScore - oScore;
      const spread = isAway ? 2.5 : -2.5;
      const atsWin = diff + spread > 0;
      const total = tScore + oScore;
      const totalLine = 220;
      const ouOver = total > totalLine;
      const gameDate = new Date(g.commence_time);
      return {
        date: (gameDate.getMonth()+1)+'/'+(gameDate.getDate()),
        opp: oppName.split(' ').pop(),
        home: !isAway,
        score: tScore+'-'+oScore,
        win, atsWin, ouOver,
        spread: atsWin ? '+'+Math.abs(diff) : '-'+Math.abs(diff),
        total: total.toString(),
        isReal: true,
      };
    }).filter(g => g.score !== '0-0');
  };

  const getH2HGamesFromScores = (scores, awayTeam, homeTeam) => {
    if(!scores||!scores.length) return [];
    const clean = (s) => (s||'').toLowerCase().trim();
    const awayClean = clean(awayTeam).split(' ').pop();
    const homeClean = clean(homeTeam).split(' ').pop();
    return scores.filter(g => {
      const a = clean(g.away_team).split(' ').pop();
      const h = clean(g.home_team).split(' ').pop();
      return (a.includes(awayClean)||awayClean.includes(a)) &&
            (h.includes(homeClean)||homeClean.includes(h)) ||
            (a.includes(homeClean)||homeClean.includes(a)) &&
            (h.includes(awayClean)||awayClean.includes(h));
    }).slice(0,10).map(g => {
      const awayScore = g.scores?.find(s => s.name === g.away_team)?.score;
      const homeScore = g.scores?.find(s => s.name === g.home_team)?.score;
      const aScore = parseInt(awayScore||0);
      const hScore = parseInt(homeScore||0);
      const awayWin = aScore > hScore;
      const gameDate = new Date(g.commence_time);
      return {
        date: (gameDate.getMonth()+1)+'/'+(gameDate.getDate()),
        opp: g.home_team.split(' ').pop(),
        home: false,
        score: aScore+'-'+hScore,
        win: awayWin, atsWin: awayWin, ouOver: (aScore+hScore) > 220,
        spread: awayWin ? '+'+Math.abs(aScore-hScore) : '-'+Math.abs(aScore-hScore),
        total: (aScore+hScore).toString(),
        isReal: true,
      };
    }).filter(g => g.score !== '0-0');
  };
   const calcSweatScore = (prop) => {
    // EV Score (35%)
    const evScore = prop.ev > 0 ? Math.min(100, 50 + prop.ev * 5) : Math.max(0, 50 + prop.ev * 3);
   
    // Line Value Score (25%)
    const lineValueScore = prop.isHRBBest ? 100 : prop.hrbDiff > 0 ? Math.max(0, 100 - prop.hrbDiff * 10) : 50;
   
    // Sharp Score (20%)
    const sharpScore = prop.lineMove > 2 ? 90 : prop.lineMove > 1 ? 70 : prop.lineMove > 0.5 ? 50 : 30;
   
    // Matchup Score (20%)
    const matchupScore = prop.matchupEdge || 50;
   
    const total = (evScore * 0.35) + (lineValueScore * 0.25) + (sharpScore * 0.20) + (matchupScore * 0.20);
    return {
      total: Math.round(total),
      evScore: Math.round(evScore),
      lineValueScore: Math.round(lineValueScore),
      sharpScore: Math.round(sharpScore),
      matchupScore: Math.round(matchupScore),
    };
  };

  const getSweatTier = (score) => {
   if(score >= 68) return {label:'🔥 Prime Sweat', color:'#ff4d6d'};
if(score >= 55) return {label:'✅ Solid Lock', color:'#00e5a0'};
if(score >= 40) return {label:'👀 Worth a Look', color:'#ffd166'};
    return {label:'❌ Pass', color:'#4a6070'};
  };

    const calcGameSweatScore = (game, sport, fanmatchData = {}, mlbContext = {}, nbaContext = {}) => {
     // console.log('calcSweat:', game?.away_team, sport, 'bartData:', bartData.length);
    //console.log('calcSweat called:', game.away_team, 'vs', game.home_team, 'sport:', sport, 'bartData:', bartData.length);
      if(!game) return null;
    const bookmakers = game.bookmakers || [];
    if(bookmakers.length < 1) return null;

    // 1. MARKET EFFICIENCY (20%)
    const spreads = bookmakers.map(bm => {
      const s = bm.markets && bm.markets.find(m => m.key==='spreads');
      return s && s.outcomes && s.outcomes[0] ? Math.abs(s.outcomes[0].point) : null;
    }).filter(x => x !== null);
    const spreadVariance = spreads.length > 1 ? Math.max(...spreads) - Math.min(...spreads) : 0;
    const marketEfficiency = Math.min(100, 40 + spreadVariance * 20);

    // 2. MODEL MISMATCH (25%)
    let modelMismatch = 50;
    let luckAdjustment = 0;
    let sosDelta = 0;
    let spreadEdge = 0;
    let efgMismatch = 0;
    let fourFactorBoost = 0;
    let predictedSpread = 0;
    let fanmatchGame = null;
    let projectedTotal = null;
    let postedTotal = null;
    let mismatchPts = 0;
    let leanSide = null;
    let leanBet = null;
    if(sport==='NCAAB') {
//console.log('BARTDATA FLORIDA:', bartData.filter(t=>t.team.toLowerCase().includes('florida')).map(t=>t.team));  
      const awayStripped = normalizeTeamName(stripMascot(game.away_team)).toLowerCase().trim();
  const homeStripped = normalizeTeamName(stripMascot(game.home_team)).toLowerCase().trim();
 //console.log('NCAAB MATCH ATTEMPT:', game.away_team, '->', awayStripped, '|', game.home_team, '->', homeStripped);
  //console.log('FANMATCH KEYS:', Object.keys(fanmatchData||{}).slice(0,5));

let fanmatchGame = null;
Object.values(fanmatchData||{}).forEach(fg => {
  const fVisitor = (fg.visitor||'').toLowerCase().trim().replace(/\./g, '');
  const fHome = (fg.home||'').toLowerCase().trim().replace(/\./g, '');
  const awayStrippedClean = awayStripped.replace(/\./g, '');
  const homeStrippedClean = homeStripped.replace(/\./g, '');

  const visitorMatch = fVisitor === awayStrippedClean || 
    fVisitor.startsWith(awayStrippedClean + ' ') || 
    awayStrippedClean.startsWith(fVisitor + ' ') ||
    awayStrippedClean.startsWith(fVisitor) ||
    fVisitor.startsWith(awayStrippedClean);
  const homeMatch = fHome === homeStrippedClean || 
    fHome.startsWith(homeStrippedClean + ' ') || 
    homeStrippedClean.startsWith(fHome + ' ') ||
    homeStrippedClean.startsWith(fHome) ||
    fHome.startsWith(homeStrippedClean);

  const visitorMatchRev = fVisitor === homeStrippedClean || 
    fVisitor.startsWith(homeStrippedClean + ' ') || 
    homeStrippedClean.startsWith(fVisitor) ||
    fVisitor.startsWith(homeStrippedClean);
  const homeMatchRev = fHome === awayStrippedClean || 
    fHome.startsWith(awayStrippedClean + ' ') || 
    awayStrippedClean.startsWith(fHome) ||
    fHome.startsWith(awayStrippedClean);

  if(visitorMatch && homeMatch) fanmatchGame = fg;
  else if(visitorMatchRev && homeMatchRev) fanmatchGame = fg;
  else if(!fanmatchGame) {
    const awayWords = awayStrippedClean.split(' ').filter(w => w.length > 2);
    const homeWords = homeStrippedClean.split(' ').filter(w => w.length > 2);
    const awayMatchesFVisitor = awayWords.every(w => fVisitor.includes(w));
    const homeMatchesFHome = homeWords.every(w => fHome.includes(w));
    const awayMatchesFHome = awayWords.every(w => fHome.includes(w));
    const homeMatchesFVisitor = homeWords.every(w => fVisitor.includes(w));
    if(awayMatchesFVisitor && homeMatchesFHome) fanmatchGame = fg;
    else if(awayMatchesFHome && homeMatchesFVisitor) fanmatchGame = fg;
  }
});

  if(fanmatchGame && fanmatchGame.homePred && fanmatchGame.visitorPred) {
    predictedSpread = fanmatchGame.homePred - fanmatchGame.visitorPred;
    // Get signed spread from home team's perspective
const allSpreadsRaw = bookmakers.map(bm => {
  const s = bm.markets && bm.markets.find(m => m.key==='spreads');
  if(!s || !s.outcomes) return null;
  const homeOutcome = s.outcomes.find(o => o.name === game.home_team);
  return homeOutcome ? homeOutcome.point : null;
}).filter(x => x !== null);
const postedSpread = allSpreadsRaw.length ? allSpreadsRaw.reduce((a,b)=>a+b,0)/allSpreadsRaw.length : null;
spreadEdge = postedSpread !== null ? predictedSpread - postedSpread : 0;

    const spreadMismatchScore = Math.min(85, 40 + Math.abs(spreadEdge) * 8);

    const awayTeam = fuzzyMatchTeam(awayStripped, bartData, 'team');
    const homeTeam = fuzzyMatchTeam(homeStripped, bartData, 'team');
    //console.log('BARTDATA MATCH:', awayStripped, '->', awayTeam?.team, '|', homeStripped, '->', homeTeam?.team);
    if(awayTeam && homeTeam) {
  sosDelta = (awayTeam.sos||0) - (homeTeam.sos||0);
  if(awayTeam.luck < -0.05) luckAdjustment += Math.min(2, Math.abs(awayTeam.luck) * 10);
  if(homeTeam.luck < -0.05) luckAdjustment -= Math.min(2, Math.abs(homeTeam.luck) * 10);

  // FOUR FACTORS CROSS-MATCHING
  // Negative diff = home team exploits away team weakness
  const EXPLOIT_THRESHOLD = 150;
  const ffMatchups = [
    { factor: 'eFG%', diff: homeTeam.eFG_O_rank - awayTeam.eFG_D_rank,
      label: `eFG% off #${homeTeam.eFG_O_rank} vs def #${awayTeam.eFG_D_rank}` },
    { factor: 'TO%',  diff: homeTeam.to_O_rank  - awayTeam.to_D_rank,
      label: `TO% off #${homeTeam.to_O_rank} vs def #${awayTeam.to_D_rank}` },
    { factor: 'OR%',  diff: homeTeam.or_O_rank  - awayTeam.or_D_rank,
      label: `OR% off #${homeTeam.or_O_rank} vs def #${awayTeam.or_D_rank}` },
    { factor: 'FTR',  diff: homeTeam.ftr_O_rank - awayTeam.ftr_D_rank,
      label: `FTR off #${homeTeam.ftr_O_rank} vs def #${awayTeam.ftr_D_rank}` },
  ];
  const exploitableEdges = ffMatchups.filter(m => m.diff < -EXPLOIT_THRESHOLD);
  fourFactorBoost = exploitableEdges.length * 4; // up to +16 pts

  // PACE / TOTALS SIGNAL
  const projPossessions = (awayTeam.tempo + homeTeam.tempo) / 2 - 2.5;
  const bothEliteDef = awayTeam.adjDERank <= 50 && homeTeam.adjDERank <= 50;
  const slowerTeamSlow = Math.max(awayTeam.tempoRank, homeTeam.tempoRank) >= 200;
  const underLean = bothEliteDef && slowerTeamSlow;
  // Use fanmatch predicted scores when available — more accurate than efficiency formula
  if(fanmatchGame && fanmatchGame.homePred && fanmatchGame.visitorPred) {
    projectedTotal = (fanmatchGame.homePred + fanmatchGame.visitorPred).toFixed(1);
  } else {
    projectedTotal = ((((awayTeam.adjOE + homeTeam.adjOE) / 2) + 
                       ((awayTeam.adjDE + homeTeam.adjDE) / 2)) / 2 / 100 * projPossessions * 2).toFixed(1);
  }

  // Store top mismatches for Jerry context
  efgMismatch = ffMatchups
    .sort((a,b) => Math.abs(b.diff) - Math.abs(a.diff))
    .slice(0,3)
    .map(m => `${m.label} (${m.diff > 0 ? '+' : ''}${m.diff})`)
    .join(' | ');

  // NET EFFICIENCY EDGE
  const netEdge = (homeTeam.adjOE - awayTeam.adjOE) + (awayTeam.adjDE - homeTeam.adjDE);
  mismatchPts = parseFloat(netEdge.toFixed(1));
}

const sosConfidence = Math.abs(sosDelta) > 2 ? Math.min(20, Math.abs(sosDelta) * 2.5) : 0;
const luckFactor = Math.min(15, Math.abs(luckAdjustment) * 7);

modelMismatch = Math.round(
  (spreadMismatchScore * 0.65) +
  (luckFactor * 0.08) +
  (sosConfidence * 0.08) +
  (fourFactorBoost * 1.0)   // direct pts boost from four factors
);
modelMismatch = Math.min(85, modelMismatch);

   } else {
    // Fanmatch not available — fall back to bartData
    const awayTeam = fuzzyMatchTeam(awayStripped, bartData, 'team');
    const homeTeam = fuzzyMatchTeam(homeStripped, bartData, 'team');
    //console.log('FALLBACK MATCH:', awayStripped, '->', awayTeam?.team, '|', homeStripped, '->', homeTeam?.team);
   if(awayTeam && homeTeam) {
  predictedSpread = (homeTeam.adjEM - awayTeam.adjEM) / 3;
  const postedSpread = spreads.length ? spreads[0] : null;
  spreadEdge = postedSpread ? predictedSpread - postedSpread : 0;
  const spreadMismatchScore = Math.min(85, 40 + Math.abs(spreadEdge) * 8);
  sosDelta = (awayTeam.sos||0) - (homeTeam.sos||0);
  const sosConfidence = Math.abs(sosDelta) > 2 ? Math.min(20, Math.abs(sosDelta) * 2.5) : 0;

  // FOUR FACTORS — fallback path
  const EXPLOIT_THRESHOLD = 150;
  const ffMatchups = [
    { factor: 'eFG%', diff: homeTeam.eFG_O_rank - awayTeam.eFG_D_rank,
      label: `eFG% off #${homeTeam.eFG_O_rank} vs def #${awayTeam.eFG_D_rank}` },
    { factor: 'TO%',  diff: homeTeam.to_O_rank  - awayTeam.to_D_rank,
      label: `TO% off #${homeTeam.to_O_rank} vs def #${awayTeam.to_D_rank}` },
    { factor: 'OR%',  diff: homeTeam.or_O_rank  - awayTeam.or_D_rank,
      label: `OR% off #${homeTeam.or_O_rank} vs def #${awayTeam.or_D_rank}` },
    { factor: 'FTR',  diff: homeTeam.ftr_O_rank - awayTeam.ftr_D_rank,
      label: `FTR off #${homeTeam.ftr_O_rank} vs def #${awayTeam.ftr_D_rank}` },
  ];
  const exploitableEdges = ffMatchups.filter(m => m.diff < -EXPLOIT_THRESHOLD);
  fourFactorBoost = exploitableEdges.length * 4;

  const projPossessions = (awayTeam.tempo + homeTeam.tempo) / 2 - 2.5;
  projectedTotal = ((((awayTeam.adjOE + homeTeam.adjOE) / 2) +
                     ((awayTeam.adjDE + homeTeam.adjDE) / 2)) / 2 / 100 * projPossessions * 2).toFixed(1);

  efgMismatch = ffMatchups
    .sort((a,b) => Math.abs(b.diff) - Math.abs(a.diff))
    .slice(0,3)
    .map(m => `${m.label} (${m.diff > 0 ? '+' : ''}${m.diff})`)
    .join(' | ');

  const netEdge = (homeTeam.adjOE - awayTeam.adjOE) + (awayTeam.adjDE - homeTeam.adjDE);
  mismatchPts = parseFloat(netEdge.toFixed(1));

  modelMismatch = Math.round(
    (spreadMismatchScore * 0.65) +
    (sosConfidence * 0.10) +
    (fourFactorBoost * 1.0) + 10
  );
  modelMismatch = Math.min(82, modelMismatch);
}
  }
   } else if(sport==='NBA') {
  //console.log('NBA context check:', Object.keys(nbaContext||{}).length, 'teams | home:', game.home_team);
    const mlOddsAway = bookmakers.map(bm => {
    const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
    return ml && ml.outcomes ? ml.outcomes.find(o => o.name===game.away_team)?.price : null;
  }).filter(x => x !== null);
  const mlOddsHome = bookmakers.map(bm => {
    const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
    return ml && ml.outcomes ? ml.outcomes.find(o => o.name===game.home_team)?.price : null;
  }).filter(x => x !== null);
  const avgAwayML = mlOddsAway.length ? mlOddsAway.reduce((a,b)=>a+b,0)/mlOddsAway.length : 0;
  const avgHomeML = mlOddsHome.length ? mlOddsHome.reduce((a,b)=>a+b,0)/mlOddsHome.length : 0;
  const mlGap = Math.abs(avgAwayML - avgHomeML);
  const spreadGap = spreads.length > 1 ? Math.max(...spreads) - Math.min(...spreads) : 0;
  modelMismatch = Math.min(80, Math.round(40 + (mlGap * 0.1) + (spreadGap * 10)));

  // BACK-TO-BACK DETECTION
  // Check if either team played yesterday by scanning gamesData for matching team names
  const yesterday = new Date(game.commence_time);
  yesterday.setDate(yesterday.getDate() - 1);
  const yesterdayStart = new Date(yesterday); yesterdayStart.setHours(0,0,0,0);
  const yesterdayEnd = new Date(yesterday); yesterdayEnd.setHours(23,59,59,999);

  const awayB2B = gamesData.some(g => {
    if(!g || !g.commence_time) return false;
    const t = new Date(g.commence_time);
    if(t < yesterdayStart || t > yesterdayEnd) return false;
    return (g.away_team === game.away_team || g.home_team === game.away_team);
  });

  const homeB2B = gamesData.some(g => {
    if(!g || !g.commence_time) return false;
    const t = new Date(g.commence_time);
    if(t < yesterdayStart || t > yesterdayEnd) return false;
    return (g.away_team === game.home_team || g.home_team === game.home_team);
  });

  // Away team on road B2B is the biggest penalty
  const awayRoadB2B = awayB2B && true; // away team is always road here
  const b2bPenalty = awayRoadB2B ? 12 : awayB2B ? 8 : homeB2B ? 6 : 0;
  const b2bTeam = awayRoadB2B ? stripMascot(game.away_team) :
                  awayB2B ? stripMascot(game.away_team) :
                  homeB2B ? stripMascot(game.home_team) : null;

  // Apply B2B penalty to modelMismatch
  if(b2bPenalty > 0) modelMismatch = Math.max(20, modelMismatch - b2bPenalty);

  // Store for Jerry context
  mismatchPts = b2bTeam ? (awayB2B ? -b2bPenalty : b2bPenalty) : 0;
  efgMismatch = b2bTeam ? `${b2bTeam} on back-to-back — fatigue factor (${awayRoadB2B ? 'road B2B' : 'home B2B'})` : '';

  // Wire in real NBA team stats if available
  const homeNBA = nbaContext[game.home_team] || 
    Object.values(nbaContext).find(t => t.team && (t.team.includes(stripMascot(game.home_team)) || stripMascot(game.home_team).includes(t.team.split(' ').pop())));
  const awayNBA = nbaContext[game.away_team] ||
    Object.values(nbaContext).find(t => t.team && (t.team.includes(stripMascot(game.away_team)) || stripMascot(game.away_team).includes(t.team.split(' ').pop())));

  if(homeNBA && awayNBA) {
    const netRatingGap = Math.abs(homeNBA.net_rating - awayNBA.net_rating);
    const netRatingBoost = Math.min(20, Math.round(netRatingGap * 1.5));
    modelMismatch = Math.min(85, modelMismatch + netRatingBoost);

    // eFG% mismatch boost
    const efgGap = Math.abs(homeNBA.efg_pct - awayNBA.efg_pct);
    if(efgGap >= 3) modelMismatch = Math.min(85, modelMismatch + 8);

    // Defensive rating boost — better defense = higher confidence
    const defRatingGap = homeNBA.defensive_rating && awayNBA.defensive_rating
      ? Math.abs(homeNBA.defensive_rating - awayNBA.defensive_rating)
      : 0;
    if(defRatingGap >= 5) modelMismatch = Math.min(85, modelMismatch + 6);
    else if(defRatingGap >= 3) modelMismatch = Math.min(85, modelMismatch + 3);

    // Home/away record boost — teams dramatically different at home vs road
    const homeWinPct = homeNBA.home_wins && homeNBA.home_losses
      ? homeNBA.home_wins / (homeNBA.home_wins + homeNBA.home_losses)
      : 0.5;
    const awayWinPct = awayNBA.away_wins && awayNBA.away_losses
      ? awayNBA.away_wins / (awayNBA.away_wins + awayNBA.away_losses)
      : 0.5;
    const situationalEdge = (homeWinPct - awayWinPct) * 20;
    if(Math.abs(situationalEdge) >= 5) {
      modelMismatch = Math.min(85, modelMismatch + Math.round(Math.abs(situationalEdge) * 0.3));
    }

    // Injury penalty — key players out dramatically affects model
    const homeHasInjury = homeNBA.injury_note && homeNBA.injury_note.includes('OUT');
    const awayHasInjury = awayNBA.injury_note && awayNBA.injury_note.includes('OUT');
    if(homeHasInjury) modelMismatch = Math.max(20, modelMismatch - 10);
    if(awayHasInjury) modelMismatch = Math.max(20, modelMismatch - 10);

    // ── OPP eFG% DEFENSIVE QUALITY ──
    const homeOppEFG = parseFloat(homeNBA.opp_efg_pct) || 0;
    const awayOppEFG = parseFloat(awayNBA.opp_efg_pct) || 0;
    if(homeOppEFG > 0 && awayOppEFG > 0) {
      const oppEFGGap = Math.abs(homeOppEFG - awayOppEFG);
      // Lower opp_efg_pct = better defense
      if(oppEFGGap >= 3) modelMismatch = Math.min(88, modelMismatch + 6);
      else if(oppEFGGap >= 1.5) modelMismatch = Math.min(85, modelMismatch + 3);
    }

    // ── PAINT DEFENSE ──
    const homeOppPaint = parseFloat(homeNBA.opp_pts_paint) || 0;
    const awayOppPaint = parseFloat(awayNBA.opp_pts_paint) || 0;
    if(homeOppPaint > 0 && awayOppPaint > 0) {
      const paintGap = Math.abs(homeOppPaint - awayOppPaint);
      if(paintGap >= 5) modelMismatch = Math.min(88, modelMismatch + 4);
    }

    // ── LAST 5 NET RATING (recent form) ──
    const homeLast5 = parseFloat(homeNBA.last_10_net_rating) || homeNBA.net_rating;
    const awayLast5 = parseFloat(awayNBA.last_10_net_rating) || awayNBA.net_rating;
    const last5Gap = Math.abs(homeLast5 - awayLast5);
    // If recent form diverges significantly from season net rating, boost confidence
    const homeFormDrift = Math.abs(homeLast5 - homeNBA.net_rating);
    const awayFormDrift = Math.abs(awayLast5 - awayNBA.net_rating);
    if(homeFormDrift >= 5 || awayFormDrift >= 5) modelMismatch = Math.min(88, modelMismatch + 5);
    if(last5Gap >= 10) modelMismatch = Math.min(88, modelMismatch + 6);
    else if(last5Gap >= 5) modelMismatch = Math.min(85, modelMismatch + 3);

    // ── NBA PROJECTED TOTAL MODEL ──
    const avgPace = (homeNBA.pace + awayNBA.pace) / 2;
    const projPoss = avgPace - 3; // venue/game adjustment

    // Cross-match offense vs opposing defense
    // Home team scores: their offense vs away defense
    const homeExpPts = projPoss * ((homeNBA.offensive_rating + awayNBA.defensive_rating) / 2) / 100;
    // Away team scores: their offense vs home defense
    const awayExpPts = projPoss * ((awayNBA.offensive_rating + homeNBA.defensive_rating) / 2) / 100;

    let nbaProjectedRaw = homeExpPts + awayExpPts;

    // eFG% efficiency adjustment — teams shooting well vs poor defenses score more
    if(homeOppEFG > 0 && awayOppEFG > 0) {
      const homeEFGAdv = homeNBA.efg_pct - awayOppEFG; // positive = home shoots better than defense allows
      const awayEFGAdv = awayNBA.efg_pct - homeOppEFG;
      const efgTotalAdj = (homeEFGAdv + awayEFGAdv) * 0.8; // each 1% eFG gap ≈ 0.8 pts
      nbaProjectedRaw += efgTotalAdj;
    }

    // Recent form drift — if both teams trending hot, bump total up
    const homeFormTrend = homeLast5 - homeNBA.net_rating; // positive = playing better recently
    const awayFormTrend = awayLast5 - awayNBA.net_rating;
    if(homeFormTrend > 3 && awayFormTrend > 3) nbaProjectedRaw += 2; // both hot
    else if(homeFormTrend < -3 && awayFormTrend < -3) nbaProjectedRaw -= 2; // both cold

    // Injury adjustment — key player OUT suppresses scoring
    if(homeHasInjury) nbaProjectedRaw -= 3;
    if(awayHasInjury) nbaProjectedRaw -= 3;

    projectedTotal = nbaProjectedRaw.toFixed(1);

    // Get posted total for comparison
    const nbaTotals = bookmakers.map(bm => {
      const t = bm.markets && bm.markets.find(m => m.key==='totals');
      return t && t.outcomes && t.outcomes[0] ? parseFloat(t.outcomes[0].point) : null;
    }).filter(x => x !== null);
    const nbaPostedTotal = nbaTotals.length ? nbaTotals.reduce((a,b)=>a+b,0)/nbaTotals.length : null;
    postedTotal = nbaPostedTotal;

    const nbaTotalDelta = nbaPostedTotal ? parseFloat(projectedTotal) - nbaPostedTotal : 0;
    const paceSignal = avgPace > 101 ? 'fast' : avgPace < 98 ? 'slow' : 'neutral';

    // Total lean for NBA — if model disagrees with market by 3+ pts
    if(nbaPostedTotal && Math.abs(nbaTotalDelta) >= 3) {
      const totalLean = nbaTotalDelta > 0 ? 'Over' : 'Under';
      // Only set total lean if no strong side lean already exists, or if total delta is huge
      if(!leanSide || Math.abs(nbaTotalDelta) >= 5) {
        leanBet = 'total';
        leanSide = `${totalLean} ${nbaPostedTotal.toFixed(1)}`;
        mismatchPts = parseFloat(nbaTotalDelta.toFixed(1));
      }
    }

    const betterTeam = homeNBA.net_rating > awayNBA.net_rating ? game.home_team : game.away_team;
    const worseTeam = homeNBA.net_rating > awayNBA.net_rating ? game.away_team : game.home_team;

    // Build efgMismatch context string
    const injuryContext = (homeNBA.injury_note || awayNBA.injury_note)
      ? ` | Injuries: ${[homeNBA.injury_note, awayNBA.injury_note].filter(Boolean).join(' / ')}`
      : '';
    const homeAwayContext = `${stripMascot(game.home_team)} ${homeNBA.home_record} home | ${stripMascot(game.away_team)} ${awayNBA.away_record} road`;

    if(!efgMismatch) {
      efgMismatch = `Net rating gap: ${netRatingGap.toFixed(1)} pts (${stripMascot(betterTeam)} +${Math.max(homeNBA.net_rating, awayNBA.net_rating).toFixed(1)} vs ${stripMascot(worseTeam)} ${Math.min(homeNBA.net_rating, awayNBA.net_rating).toFixed(1)}) | ${homeAwayContext}${injuryContext}`;
    }

    // Lean side — composite score using all available data
    if(!b2bTeam) {
      // Net rating (season) weighted 40%
      let homeLeanScore = homeNBA.net_rating * 0.4;
      let awayLeanScore = awayNBA.net_rating * 0.4;

      // Last 5 net rating (recent form) weighted 30%
      homeLeanScore += homeLast5 * 0.3;
      awayLeanScore += awayLast5 * 0.3;

      // Home/away situational edge weighted 20%
      homeLeanScore += situationalEdge * 0.2;
      awayLeanScore -= situationalEdge * 0.2;

      // Defensive rating bonus weighted 10% (lower = better)
      if(homeNBA.defensive_rating && awayNBA.defensive_rating) {
        const defAdv = (awayNBA.defensive_rating - homeNBA.defensive_rating) * 0.1;
        homeLeanScore += defAdv;
        awayLeanScore -= defAdv;
      }

      // Injury penalties
      if(homeHasInjury) homeLeanScore -= 6;
      if(awayHasInjury) awayLeanScore -= 6;

      // B2B fatigue
      if(awayB2B) awayLeanScore -= 4;
      if(homeB2B) homeLeanScore -= 3;

      leanSide = homeLeanScore > awayLeanScore
        ? stripMascot(game.home_team)
        : stripMascot(game.away_team);
      leanBet = 'ml';
    }
  }
// Playoff adjustments
if(isPlayoffMode) {
  const series = playoffSeries?.[game.home_team] || playoffSeries?.[game.away_team];
  if(series) {
    if(series.leader === game.home_team && series.leader_wins > series.trailer_wins) {
      modelMismatch = Math.min(88, modelMismatch + 8);
    }
    if(series.is_elimination) {
      modelMismatch = Math.min(88, modelMismatch + 5);
    }
  }
}
// NBA fallback lean — use moneyline favorite if no NBA data
  if(!leanSide) {
    const mlOddsAwayNBA = bookmakers.map(bm => {
      const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
      return ml && ml.outcomes ? ml.outcomes.find(o => o.name===game.away_team)?.price : null;
    }).filter(x => x !== null);
    const mlOddsHomeNBA = bookmakers.map(bm => {
      const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
      return ml && ml.outcomes ? ml.outcomes.find(o => o.name===game.home_team)?.price : null;
    }).filter(x => x !== null);
    const avgAwayMLNBA = mlOddsAwayNBA.length ? mlOddsAwayNBA.reduce((a,b)=>a+b,0)/mlOddsAwayNBA.length : 0;
    const avgHomeMLNBA = mlOddsHomeNBA.length ? mlOddsHomeNBA.reduce((a,b)=>a+b,0)/mlOddsHomeNBA.length : 0;
    if(avgAwayMLNBA && avgHomeMLNBA) {
      leanSide = avgHomeMLNBA < avgAwayMLNBA 
        ? stripMascot(game.home_team) 
        : stripMascot(game.away_team);
      leanBet = 'ml';
    }
  }
} else if(sport==='MLB') {
  // Base market signals
  const spreadGap = spreads.length > 1 ? Math.max(...spreads) - Math.min(...spreads) : 0;
  const totals = bookmakers.map(bm => {
    const t = bm.markets && bm.markets.find(m => m.key==='totals');
    return t && t.outcomes && t.outcomes[0] ? parseFloat(t.outcomes[0].point) : null;
  }).filter(x => x !== null);
  const totalGap = totals.length > 1 ? Math.max(...totals) - Math.min(...totals) : 0;
  const avgTotal = totals.length ? totals.reduce((a,b)=>a+b,0)/totals.length : 9.0;
  const mlOddsAway = bookmakers.map(bm => {
    const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
    return ml && ml.outcomes ? ml.outcomes.find(o => o.name===game.away_team)?.price : null;
  }).filter(x => x !== null);
  const mlOddsHome = bookmakers.map(bm => {
    const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
    return ml && ml.outcomes ? ml.outcomes.find(o => o.name===game.home_team)?.price : null;
  }).filter(x => x !== null);
  const avgAwayML = mlOddsAway.length ? mlOddsAway.reduce((a,b)=>a+b,0)/mlOddsAway.length : 0;
  const avgHomeML = mlOddsHome.length ? mlOddsHome.reduce((a,b)=>a+b,0)/mlOddsHome.length : 0;
  const mlGap = Math.abs(avgAwayML - avgHomeML);

  // Base score from market signals
  modelMismatch = Math.min(75, Math.round(35 + (mlGap * 0.08) + (spreadGap * 8) + (totalGap * 6)));

  // Get MLB context from pre-fetched data
  const mlbCtx = mlbContext?.[game.id] || 
                 mlbContext?.[game.home_team] ||
                 mlbContext?.[game.away_team] ||
                 null;

  if(mlbCtx) {
    // ── PROJECTED TOTAL vs MARKET LINE ──
    if(mlbCtx.projected_total && avgTotal) {
      const totalDeltaAbs = Math.abs(mlbCtx.projected_total - avgTotal);
      if(totalDeltaAbs >= 2) modelMismatch = Math.min(88, modelMismatch + 15);
      else if(totalDeltaAbs >= 1) modelMismatch = Math.min(85, modelMismatch + 8);
      else if(totalDeltaAbs >= 0.5) modelMismatch = Math.min(80, modelMismatch + 4);
      projectedTotal = mlbCtx.projected_total.toString();
      postedTotal = avgTotal;
      mismatchPts = mlbCtx.projected_total - avgTotal;
    }

    // ── PITCHER QUALITY GAP ──
    const homeXera = mlbCtx.home_sp_xera && parseFloat(mlbCtx.home_sp_xera) <= 6.5 ? parseFloat(mlbCtx.home_sp_xera) : 0;
    const awayXera = mlbCtx.away_sp_xera && parseFloat(mlbCtx.away_sp_xera) <= 6.5 ? parseFloat(mlbCtx.away_sp_xera) : 0;
    if(homeXera > 0 && awayXera > 0) {
      const xeraGap = Math.abs(homeXera - awayXera);
      if(xeraGap >= 1.5) modelMismatch = Math.min(88, modelMismatch + 10);
      else if(xeraGap >= 0.75) modelMismatch = Math.min(85, modelMismatch + 5);
      const betterPitcherTeam = homeXera < awayXera ? game.home_team : game.away_team;
      efgMismatch = `Pitcher edge: ${stripMascot(betterPitcherTeam)} xERA ${Math.min(homeXera, awayXera).toFixed(2)} vs ${Math.max(homeXera, awayXera).toFixed(2)}`;
    }

    // ── K RATE GAP (pitcher K% vs lineup K%) ──
    const homeKGap = parseFloat(mlbCtx.home_k_gap) || 0;
    const awayKGap = parseFloat(mlbCtx.away_k_gap) || 0;
    const maxKGap = Math.max(Math.abs(homeKGap), Math.abs(awayKGap));
    if(maxKGap >= 8) modelMismatch = Math.min(88, modelMismatch + 8);
    else if(maxKGap >= 4) modelMismatch = Math.min(85, modelMismatch + 4);

    // ── wOBA / wRC+ OFFENSIVE QUALITY GAP ──
    const homeWRC = parseFloat(mlbCtx.home_wrc_plus) || 100;
    const awayWRC = parseFloat(mlbCtx.away_wrc_plus) || 100;
    const homeWOBA = parseFloat(mlbCtx.home_woba) || 0;
    const awayWOBA = parseFloat(mlbCtx.away_woba) || 0;
    const wrcGap = Math.abs(homeWRC - awayWRC);
    if(wrcGap >= 20) modelMismatch = Math.min(88, modelMismatch + 8);
    else if(wrcGap >= 10) modelMismatch = Math.min(85, modelMismatch + 4);

    // ── PLATOON ADVANTAGE ──
    const homePlatoon = parseFloat(mlbCtx.home_platoon_advantage) || 0;
    const awayPlatoon = parseFloat(mlbCtx.away_platoon_advantage) || 0;
    const maxPlatoon = Math.max(Math.abs(homePlatoon), Math.abs(awayPlatoon));
    if(maxPlatoon >= 5) modelMismatch = Math.min(88, modelMismatch + 6);
    else if(maxPlatoon >= 3) modelMismatch = Math.min(85, modelMismatch + 3);

    // ── BULLPEN ERA GAP ──
    const homeBullpen = parseFloat(mlbCtx.home_bullpen_era) || 0;
    const awayBullpen = parseFloat(mlbCtx.away_bullpen_era) || 0;
    if(homeBullpen > 0 && awayBullpen > 0) {
      const bullpenGap = Math.abs(homeBullpen - awayBullpen);
      if(bullpenGap >= 1.5) modelMismatch = Math.min(88, modelMismatch + 5);
      else if(bullpenGap >= 0.75) modelMismatch = Math.min(85, modelMismatch + 2);
    }

    // ── DAYS REST ADVANTAGE ──
    const homeDaysRest = parseInt(mlbCtx.home_days_rest) || 4;
    const awayDaysRest = parseInt(mlbCtx.away_days_rest) || 4;
    if(homeDaysRest >= 5 && awayDaysRest <= 3) modelMismatch = Math.min(88, modelMismatch + 5);
    else if(awayDaysRest >= 5 && homeDaysRest <= 3) modelMismatch = Math.min(88, modelMismatch + 5);

    // ── WEATHER SIGNAL ──
    const temp = parseFloat(mlbCtx.temperature) || 72;
    const windSpeed = parseFloat(mlbCtx.wind_speed) || 0;
    const windDir = mlbCtx.wind_direction || '';
    if(temp <= 45) modelMismatch = Math.min(88, modelMismatch + 4); // cold suppresses scoring
    if(windSpeed >= 12 && windDir.includes('out')) modelMismatch = Math.min(88, modelMismatch + 5); // wind blowing out
    if(windSpeed >= 12 && windDir.includes('in')) modelMismatch = Math.min(88, modelMismatch + 4); // wind blowing in

    // ── PARK FACTOR ──
    if(mlbCtx.park_run_factor) {
      if(mlbCtx.park_run_factor >= 110 || mlbCtx.park_run_factor <= 93) {
        modelMismatch = Math.min(88, modelMismatch + 8);
        if(!efgMismatch) efgMismatch = `${mlbCtx.venue} park factor: ${mlbCtx.park_run_factor} (${mlbCtx.park_run_factor >= 110 ? 'hitter park' : 'pitcher park'})`;
      }
    }

    // ── UMPIRE SIGNAL ──
    if(mlbCtx.umpire_note && mlbCtx.umpire_note.includes('K-friendly')) {
      modelMismatch = Math.min(88, modelMismatch + 5);
    }
    if(mlbCtx.umpire_note && mlbCtx.umpire_note.includes('over')) {
      modelMismatch = Math.min(88, modelMismatch + 3);
    }

    // ── NRFI CONVICTION BOOST (recalibrated from 235-game audit) ──
    // 90-94: 73.3% (prime), 95+: 47% (volatile), 75-79: 60.9%, 70-74: 59.4%
    // 80-89: 42.5% (no edge — no boost), <=40: 77.8% YRFI hit
    if(mlbCtx.nrfi_score) {
      const nrfi = mlbCtx.nrfi_score;
      if(nrfi >= 95) modelMismatch = Math.min(85, modelMismatch + 3);        // volatile — minimal boost
      else if(nrfi >= 90) modelMismatch = Math.min(88, modelMismatch + 10); // prime sweet spot
      else if(nrfi >= 70 && nrfi <= 79) modelMismatch = Math.min(85, modelMismatch + 5); // mild lean (60% hit)
      else if(nrfi <= 35) modelMismatch = Math.min(85, modelMismatch + 5);  // strong YRFI signal
      else if(nrfi <= 40) modelMismatch = Math.min(85, modelMismatch + 3);  // moderate YRFI
      // 41-69 and 80-89 = no boost (no edge in audit)
    }

    // ── SPREAD DELTA BOOST ── (60% win rate at 3+ delta)
    const spreadDelta = mlbCtx.spread_delta != null ? Math.abs(parseFloat(mlbCtx.spread_delta)) : 0;
    if(spreadDelta >= 4.0) modelMismatch = Math.min(92, modelMismatch + 12);
    else if(spreadDelta >= 3.0) modelMismatch = Math.min(88, modelMismatch + 8);

    // ── LEAN SIDE ──
    // Primary: ML lean from spread delta 3+ (60% win rate — proven edge)
    if(spreadDelta >= 3.0 && mlbCtx.spread_delta != null) {
      const delta = parseFloat(mlbCtx.spread_delta);
      leanSide = delta > 0 ? stripMascot(game.home_team) + ' ML' : stripMascot(game.away_team) + ' ML';
      leanBet = 'ml';
    }
    // Secondary: over lean (55.9% hit rate)
    if(!leanSide && mlbCtx.over_lean === true) {
      leanBet = 'total';
      leanSide = `Over ${avgTotal.toFixed(1)}`;
    }
    // Tertiary: NRFI sweet spot (77% hit rate)
    if(!leanSide && mlbCtx.nrfi_score >= 88 && mlbCtx.nrfi_score <= 94) {
      leanSide = 'NRFI';
      leanBet = 'nrfi';
    }

  } else {
    leanSide = null;
    leanBet = null;
  }
} else if(sport==='NHL') {
  // NHL market signals
  const spreadGap = spreads.length > 1 ? Math.max(...spreads) - Math.min(...spreads) : 0;
  const mlOddsAway = bookmakers.map(bm => {
    const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
    return ml && ml.outcomes ? ml.outcomes.find(o => o.name===game.away_team)?.price : null;
  }).filter(x => x !== null);
  const mlOddsHome = bookmakers.map(bm => {
    const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
    return ml && ml.outcomes ? ml.outcomes.find(o => o.name===game.home_team)?.price : null;
  }).filter(x => x !== null);
  const avgAwayML = mlOddsAway.length ? mlOddsAway.reduce((a,b)=>a+b,0)/mlOddsAway.length : 0;
  const avgHomeML = mlOddsHome.length ? mlOddsHome.reduce((a,b)=>a+b,0)/mlOddsHome.length : 0;
  const mlGap = Math.abs(avgAwayML - avgHomeML);
  modelMismatch = Math.min(70, Math.round(35 + (mlGap * 0.08) + (spreadGap * 8)));
  // No default lean — only lean when model has real signal
  leanSide = null;
  leanBet = null;
} else {
  modelMismatch = 45;
}

    // 3. LINE TRAJECTORY (20%)
    const allSpreads = bookmakers.map(bm => {
      const s = bm.markets && bm.markets.find(m => m.key==='spreads');
      return s && s.outcomes && s.outcomes[0] ? s.outcomes[0].point : null;
    }).filter(x => x !== null);
   const lineRange = allSpreads.length > 1 ? Math.max(...allSpreads) - Math.min(...allSpreads) : 0;
   const gameKey = game.id || (game.away_team+game.home_team);
    const histData = historicalOdds[gameKey];
    let lineTrajectory = lineRange >= 2 ? 85 : lineRange >= 1 ? 65 : lineRange >= 0.5 ? 50 : 35;
    let lineMoveDirection = null;
    let lineMoveTeam = null;
    let lineMovePoints = 0;
    if(histData?.openingSpread !== null && histData?.openingSpread !== undefined && allSpreads.length > 0) {
      const currentSpread = allSpreads[0];
      const realMovement = Math.abs(histData.openingSpread - currentSpread);
      lineMovePoints = parseFloat(realMovement.toFixed(1));
      lineTrajectory = realMovement >= 3 ? 95 : realMovement >= 2 ? 85 : realMovement >= 1 ? 70 : realMovement >= 0.5 ? 55 : 35;
      // Determine direction — if current spread is smaller (less negative) than opening, home team got sharper
      if(realMovement >= 0.5) {
        const openingSpread = histData.openingSpread;
        if(currentSpread < openingSpread) {
          lineMoveTeam = stripMascot(game.home_team);
          lineMoveDirection = 'home';
        } else {
          lineMoveTeam = stripMascot(game.away_team);
          lineMoveDirection = 'away';
        }
      }
    }

    // 4. SHARP SIGNAL (20%)
    const mlOdds = bookmakers.map(bm => {
      const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
      return ml && ml.outcomes && ml.outcomes[0] ? ml.outcomes[0].price : null;
    }).filter(x => x !== null);
    const mlVariance = mlOdds.length > 1 ? Math.max(...mlOdds) - Math.min(...mlOdds) : 0;
    const sharpSignal = mlVariance > 20 ? 80 : mlVariance > 10 ? 60 : mlVariance > 5 ? 45 : 30;

    // 5. SITUATIONAL EDGE
    let situationalEdge = 50;

    // MLB situational factors from pipeline
    if(sport==='MLB' && mlbContext) {
      const mlbCtx = mlbContext?.[game.home_team] || mlbContext?.[game.away_team] ||
        Object.values(mlbContext).find((ctx: any) => ctx.home_team === game.home_team || ctx.away_team === game.away_team) as any;
      if(mlbCtx) {
        let sitScore = 30;

        // Platoon advantage — confirmed lineup handedness vs pitcher hand
        const homePlatoon = parseFloat(mlbCtx.home_platoon_advantage) || 0;
        const awayPlatoon = parseFloat(mlbCtx.away_platoon_advantage) || 0;
        const platoonGap = Math.abs(homePlatoon - awayPlatoon);
        if(platoonGap >= 5) sitScore += 15;
        else if(platoonGap >= 3) sitScore += 8;
        else if(platoonGap >= 1) sitScore += 3;

        // Platoon-adjusted wRC+ differential — team's actual performance vs opposing hand
        const homeWrcVsHand = parseFloat(mlbCtx.home_wrc_vs_opp_hand);
        const awayWrcVsHand = parseFloat(mlbCtx.away_wrc_vs_opp_hand);
        const homeWrcSeason = parseFloat(mlbCtx.home_wrc_plus) || 100;
        const awayWrcSeason = parseFloat(mlbCtx.away_wrc_plus) || 100;
        if(!isNaN(homeWrcVsHand) && Math.abs(homeWrcVsHand - homeWrcSeason) >= 15) sitScore += 6;
        if(!isNaN(awayWrcVsHand) && Math.abs(awayWrcVsHand - awayWrcSeason) >= 15) sitScore += 6;

        // Pitcher L3 form drift — recent form vs season xERA
        const homeXera = parseFloat(mlbCtx.home_sp_xera);
        const awayXera = parseFloat(mlbCtx.away_sp_xera);
        const homeL3 = parseFloat(mlbCtx.home_pitcher_last_3_era);
        const awayL3 = parseFloat(mlbCtx.away_pitcher_last_3_era);
        if(!isNaN(homeXera) && !isNaN(homeL3) && Math.abs(homeL3 - homeXera) >= 1.5) sitScore += 5;
        if(!isNaN(awayXera) && !isNaN(awayL3) && Math.abs(awayL3 - awayXera) >= 1.5) sitScore += 5;

        // Team defense gap (OAA) — defense saves 10-15 runs over 162g
        const homeOaa = parseFloat(mlbCtx.home_team_oaa);
        const awayOaa = parseFloat(mlbCtx.away_team_oaa);
        if(!isNaN(homeOaa) && !isNaN(awayOaa)) {
          const oaaGap = Math.abs(homeOaa - awayOaa);
          if(oaaGap >= 15) sitScore += 8;
          else if(oaaGap >= 10) sitScore += 5;
          else if(oaaGap >= 5) sitScore += 2;
        }

        // Catcher framing gap — 5+ runs = K prop + total implication
        const homeFraming = parseFloat(mlbCtx.home_catcher_framing);
        const awayFraming = parseFloat(mlbCtx.away_catcher_framing);
        if(!isNaN(homeFraming) && !isNaN(awayFraming) && Math.abs(homeFraming - awayFraming) >= 5) sitScore += 4;

        // Expected vs actual wOBA divergence — regression signal
        const homeXwoba = parseFloat(mlbCtx.home_team_xwoba);
        const awayXwoba = parseFloat(mlbCtx.away_team_xwoba);
        const homeWoba = parseFloat(mlbCtx.home_woba);
        const awayWoba = parseFloat(mlbCtx.away_woba);
        if(!isNaN(homeXwoba) && !isNaN(homeWoba) && Math.abs(homeXwoba - homeWoba) >= 0.020) sitScore += 3;
        if(!isNaN(awayXwoba) && !isNaN(awayWoba) && Math.abs(awayXwoba - awayWoba) >= 0.020) sitScore += 3;

        // Team streaks — hot/cold teams
        const homeStreak = mlbCtx.home_streak || '';
        const awayStreak = mlbCtx.away_streak || '';
        const homeStreakNum = parseInt(homeStreak.replace(/[WL]/,'')) || 0;
        const awayStreakNum = parseInt(awayStreak.replace(/[WL]/,'')) || 0;
        if(homeStreakNum >= 4 || awayStreakNum >= 4) sitScore += 10;
        else if(homeStreakNum >= 3 || awayStreakNum >= 3) sitScore += 5;

        // Days rest — pitcher rest advantage
        const homeRest = parseInt(mlbCtx.home_days_rest) || 4;
        const awayRest = parseInt(mlbCtx.away_days_rest) || 4;
        const restGap = Math.abs(homeRest - awayRest);
        if(restGap >= 3) sitScore += 8;
        else if(restGap >= 2) sitScore += 4;

        // Travel / road fatigue from XGBoost features
        const tzChange = parseInt(mlbCtx.timezone_change) || 0;
        const consecRoad = parseInt(mlbCtx.away_consecutive_road_games) || 0;
        const travelDist = parseInt(mlbCtx.home_travel_distance_last_game) || 0;
        if(tzChange >= 2) sitScore += 5;
        if(consecRoad >= 6) sitScore += 8;
        else if(consecRoad >= 4) sitScore += 4;
        if(travelDist >= 1500) sitScore += 4;

        situationalEdge = Math.min(90, sitScore);
      }
    }

    if(sport==='NCAAB' && bartData.length) {
      const awayT = fuzzyMatchTeam(stripMascot(game.away_team), bartData, 'team');
      const homeT = fuzzyMatchTeam(stripMascot(game.home_team), bartData, 'team');
      if(awayT && homeT) {
        // SOS gap — bigger gap = more interesting matchup
        const sosGap = Math.abs((awayT.sos||0) - (homeT.sos||0));
        const sosEdge = Math.min(25, sosGap * 2);
        // Luck regression — lucky team playing unlucky team = edge
        const luckDiff = Math.abs((awayT.luck||0) - (homeT.luck||0));
        const luckEdge = Math.min(25, luckDiff * 100);
        // Home court advantage factor
        const adjEMGap = Math.abs((awayT.adjEM||0) - (homeT.adjEM||0));
        const hcaEdge = adjEMGap > 10 ? 20 : adjEMGap > 5 ? 15 : adjEMGap > 2 ? 10 : 5;
        situationalEdge = Math.min(90, Math.round(30 + sosEdge + luckEdge + hcaEdge));
      }
    }

   // Sport-specific weights — tuned per sport based on data quality
   const w = sport === 'MLB'
     ? {market: 0.08, model: 0.45, line: 0.12, sharp: 0.10, situation: 0.25}
     // MLB: deepest pipeline (xERA, K gap, wRC+, park, weather, umpire, spread proj, NRFI) → model dominates
     : sport === 'NBA'
     ? {market: 0.10, model: 0.40, line: 0.15, sharp: 0.10, situation: 0.25}
     // NBA: strong pipeline (net rating, DefRtg, pace, injuries, projected total) → model heavy
     : sport === 'NCAAB'
     ? {market: 0.10, model: 0.35, line: 0.15, sharp: 0.15, situation: 0.25}
     // NCAAB: KenPom efficiency model + fanmatch → model + situation heavy
     : sport === 'NHL'
     ? {market: 0.25, model: 0.15, line: 0.25, sharp: 0.25, situation: 0.10}
     // NHL: no pipeline model → market signals + line movement dominate
     : sport === 'NFL'
     ? {market: 0.20, model: 0.15, line: 0.25, sharp: 0.25, situation: 0.15}
     // NFL: no pipeline model → sharp money + line movement key
     : sport === 'UFC'
     ? {market: 0.20, model: 0.20, line: 0.15, sharp: 0.30, situation: 0.15}
     // UFC: fighter stats in pipeline but thin → sharp money most reliable
     : {market: 0.20, model: 0.20, line: 0.20, sharp: 0.20, situation: 0.20};

   let rawTotal = Math.round(
     (marketEfficiency * w.market) +
     (modelMismatch * w.model) +
     (lineTrajectory * w.line) +
     (sharpSignal * w.sharp) +
     (situationalEdge * w.situation)
   );

   // Normalization curve — compresses scores so Prime Sweats are rare
   // Maps 0-100 raw → 0-100 display with compression in the 50-80 range
   // Raw 40 → ~38, Raw 55 → ~50, Raw 65 → ~60, Raw 75 → ~68, Raw 85 → ~76, Raw 95 → ~88
   let total;
   if(rawTotal <= 40) {
     total = rawTotal; // low scores pass through
   } else if(rawTotal <= 90) {
     // Compress the 40-90 range into 40-80 — makes 68+ (Prime Sweat) harder to hit
     total = Math.round(40 + ((rawTotal - 40) / 50) * 40 * (0.7 + (rawTotal - 40) / 50 * 0.3));
   } else {
     total = Math.round(80 + ((rawTotal - 90) / 10) * 12); // 90-100 raw → 80-92 display
   }
   total = Math.min(95, total); // absolute cap

// TOTAL SIGNAL BOOST — when projected vs posted delta is significant
let totalBetDirection = null;
let totalBoost = 0;
let totalIsPrimary = false;
if(sport === 'NCAAB' && projectedTotal && postedTotal) {
  const delta = parseFloat(projectedTotal) - parseFloat(postedTotal);
  if(Math.abs(delta) >= 6) {
    totalBoost = 12;
    totalBetDirection = delta < 0 ? 'Under' : 'Over';
  } else if(Math.abs(delta) >= 4) {
    totalBoost = 7;
    totalBetDirection = delta < 0 ? 'Under' : 'Over';
  } else if(Math.abs(delta) >= 2) {
    totalBoost = 3;
    totalBetDirection = delta < 0 ? 'Under' : 'Over';
  }
  total = Math.min(95, total + totalBoost);
  // Total becomes primary when its boost exceeds the four factor spread signal
  // AND the delta is significant enough (4+ pts)
  totalIsPrimary = totalBoost >= 7 && totalBoost >= fourFactorBoost;
}

// Cap score for low-major matchups
if(sport==='NCAAB' && bartData.length) {
  const awayT = fuzzyMatchTeam(stripMascot(game.away_team), bartData, 'team');
  const homeT = fuzzyMatchTeam(stripMascot(game.home_team), bartData, 'team');
  if(awayT && homeT) {
    const avgOE = (awayT.adjOE + homeT.adjOE) / 2;
    if(avgOE < 100) total = Math.min(total, 52);
    else if(avgOE < 108) total = Math.min(total, 65);
  }
}

    // Generate narrative
    const narrative = generateSweatNarrative(game, sport, {
  total, marketEfficiency, modelMismatch, lineTrajectory, sharpSignal,
  projectedTotal, postedTotal, mismatchPts, spreadVariance, lineRange,
});
// Store breakdown for Jerry
const awayTeamData = sport === 'NCAAB' ? fuzzyMatchTeam(stripMascot(game.away_team).toLowerCase().trim(), bartData, 'team') : null;
const homeTeamData = sport === 'NCAAB' ? fuzzyMatchTeam(stripMascot(game.home_team).toLowerCase().trim(), bartData, 'team') : null;

const ncaabBreakdown = sport === 'NCAAB' ? {
  luckAdjustment, sosDelta, spreadEdge: spreadEdge||0,
  predictedSpread: predictedSpread||0,
  hasFanmatch: !!fanmatchGame,
  homeWP: fanmatchGame?.homeWP || null,
  awayTeamData, homeTeamData,
  fourFactorBoost,
  efgMismatch,
  projectedTotal,
  mismatchPts,
  totalSignalBet: totalBetDirection,
  totalBoost,
  totalIsPrimary,
  lineMoveDirection,
  lineMoveTeam,
  lineMovePoints,
} : {};
    // Best bets
    const hrbLine = getHRBLine(game);
    const bestBook = bookmakers.reduce((best, bm) => {
      const s = bm.markets && bm.markets.find(m => m.key==='spreads');
      if(!s || !s.outcomes) return best;
      const pt = Math.abs(s.outcomes[0]?.point||99);
      return pt < best.pt ? {bm, pt} : best;
    }, {bm: bookmakers[0], pt: 99});
    // Fallback to best available book if HRB not available
    const getBestOutcome = (marketKey) => {
      if(hrbLine && hrbLine[marketKey==='spreads'?'spread':marketKey==='totals'?'total':'ml']?.[0]) {
        return {outcome: hrbLine[marketKey==='spreads'?'spread':marketKey==='totals'?'total':'ml'][0], book: HRB};
      }
      let best = null, bestOdds = -9999, bestBook = '';
      bookmakers.forEach(bm => {
        const mkt = bm.markets && bm.markets.find(m => m.key===marketKey);
        if(!mkt || !mkt.outcomes || !mkt.outcomes[0]) return;
        if(mkt.outcomes[0].price > bestOdds) {
          bestOdds = mkt.outcomes[0].price;
          best = mkt.outcomes[0];
          bestBook = BOOKMAKER_MAP[bm.key]||bm.key;
        }
      });
      return best ? {outcome: best, book: bestBook} : null;
    };
    const spreadResult = getBestOutcome('spreads');
    const totalResult = getBestOutcome('totals');
    const mlResult = getBestOutcome('h2h');
    const spreadLine = spreadResult?.outcome;
    const spreadBook = spreadResult?.book;
    const totalLine = totalResult?.outcome;
    const totalBook = totalResult?.book;
    const mlLine = mlResult?.outcome;
    const mlBook = mlResult?.book;

    // Directional lean — leanSide/leanBet set in sport blocks above
    const totalMismatchPts = projectedTotal && postedTotal ? Math.abs(projectedTotal - postedTotal) : 0;
    if(false) {
      // Total leans disabled until dedicated model is built
    } else {

      const awayML = bookmakers.map(bm => {
        const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
        return ml && ml.outcomes ? ml.outcomes.find(o => o.name===game.away_team)?.price : null;
      }).filter(x => x !== null);
      const homeML = bookmakers.map(bm => {
        const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
        return ml && ml.outcomes ? ml.outcomes.find(o => o.name===game.home_team)?.price : null;
      }).filter(x => x !== null);
      const avgAwayML = awayML.length ? awayML.reduce((a,b)=>a+b,0)/awayML.length : 0;
      const avgHomeML = homeML.length ? homeML.reduce((a,b)=>a+b,0)/homeML.length : 0;
      if(avgAwayML && avgHomeML) {
  if(sport === 'NCAAB') {
  let favTeam;
  if(spreadEdge !== 0) {
    favTeam = spreadEdge > 0 ? stripMascot(game.home_team) : stripMascot(game.away_team);
  } else {
    favTeam = avgAwayML < avgHomeML ? stripMascot(game.away_team) : stripMascot(game.home_team);
  }
  const favSpread = allSpreads.length ? (allSpreads.map(Math.abs).reduce((a,b)=>a+b,0)/allSpreads.length).toFixed(1) : null;
  leanSide = favTeam+(favSpread ? ' -'+favSpread : '');
  leanBet = 'spread';
}
}
    }
    return {
  total,
  leanSide, leanBet,
  totalIsPrimary,
  lineMoveDirection, lineMoveTeam, lineMovePoints,
      marketEfficiency: Math.round(marketEfficiency),
      modelMismatch: Math.round(modelMismatch),
      lineTrajectory: Math.round(lineTrajectory),
      sharpSignal: Math.round(sharpSignal),
      situationalEdge: Math.round(situationalEdge),
      narrative,
      spreadBet: spreadLine ? {
        pick: spreadLine.name+' '+(spreadLine.point>0?'+':'')+spreadLine.point,
        odds: spreadLine.price,
        book: spreadBook||HRB,
      } : null,
      totalBet: totalLine ? {
  pick: (totalBetDirection || (projectedTotal && postedTotal && parseFloat(projectedTotal) > parseFloat(postedTotal) ? 'Over' : 'Under')) + ' ' + totalLine.point,
  odds: totalLine.price,
  book: totalBook||HRB,
  isSignal: totalBoost >= 7,
  isPrimary: totalIsPrimary,
} : null,
      mlBet: mlLine ? {
        pick: mlLine.name+' ML',
        odds: mlLine.price,
        book: mlBook||HRB,
      } : null,
      projectedTotal, postedTotal, mismatchPts,
      ...ncaabBreakdown,
    };
  };

  const generateSweatNarrative = (game, sport, data) => {
    const away = game.away_team.split(' ').pop();
    const home = game.home_team.split(' ').pop();
    const sentences = [];

    // Sentence 1 — Model/efficiency insight
    if(sport==='NCAAB') {
  const edge = data.spreadEdge || 0;
  const absDiff = Math.abs(edge).toFixed(1);
  const side = edge > 0 ? game.home_team.split(' ').pop() : game.away_team.split(' ').pop();
  if(Math.abs(edge) >= 2) {
    sentences.push(`Our efficiency model sees a ${absDiff}-point edge favoring ${side} vs the posted spread.`);
  } else {
    sentences.push(`Our efficiency model projects this matchup close to the posted spread — market appears fairly priced.`);
  }

    } else if(sport==='NBA') {
      sentences.push(`${away} and ${home} present an NBA matchup with ${data.spreadVariance > 1 ? 'notable spread variance across books suggesting market uncertainty' : 'tight consensus across books'}.`);
    } else if(sport==='NFL') {
      sentences.push(`NFL lines show ${data.spreadVariance > 1.5 ? 'significant disagreement between books — a potential sharp opportunity' : 'tight consensus suggesting an efficient market'}.`);
    } else if(sport==='NHL') {
      sentences.push(`${away} vs ${home} shows ${data.mlVariance > 15 ? 'moneyline variance suggesting sharp action on one side' : 'efficient pricing across books'}.`);
    } else if(sport==='MLB') {
      sentences.push(`${away} vs ${home} — MLB lines ${data.spreadVariance > 0.5 ? 'show run line disagreement between books' : 'are tightly priced across the market'}.`);
    } else {
      sentences.push(`${away} vs ${home} — market analysis shows ${data.spreadVariance > 1 ? 'inefficiency worth exploiting' : 'efficient pricing with limited edge'}.`);
    }

    // Sentence 2 — Line movement
    if(data.lineRange >= 2) {
      sentences.push(`A ${data.lineRange.toFixed(1)}-point spread between books signals significant sharp money movement — strong reverse line action detected.`);
    } else if(data.lineRange >= 1) {
      sentences.push(`${data.lineRange.toFixed(1)}-point line variance across books indicates professional money has moved this line.`);
    } else if(data.lineRange >= 0.5) {
      sentences.push(`Moderate line movement of ${data.lineRange.toFixed(1)} points detected — some sharp interest but market remains relatively stable.`);
    } else {
      sentences.push(`Lines are stable across books with minimal movement — public betting market with no clear sharp signal.`);
    }

    // Sentence 3 — Recommendation
    if(data.total >= 75) {
      sentences.push(`Overall model confidence is high — this game has multiple edges aligning. Hard Rock has competitive pricing on this matchup.`);
    } else if(data.total >= 60) {
      sentences.push(`Moderate edge detected. Worth a play at the right number — shop for the best available line before the market moves.`);
    } else {
      sentences.push(`Limited edge on this matchup. Consider waiting for better line value or targeting a different market.`);
    }

    if(sport==='NCAAB') {
      const awayTeamData = fuzzyMatchTeam(stripMascot(game.away_team), bartData, 'team');
      const homeTeamData = fuzzyMatchTeam(stripMascot(game.home_team), bartData, 'team');
      if(awayTeamData && homeTeamData) {
        const avgOE = (awayTeamData.adjOE + homeTeamData.adjOE) / 2;
        if(avgOE < 100) sentences.push('⚠️ Low-major matchup — model confidence reduced due to limited efficiency data on these programs.');
      }
    }
    if(sport==='NCAAB' && data.projectedTotal && data.postedTotal && data.mismatchPts > 15) {
      sentences.push('⚠️ Large model discrepancy detected — exercise caution as this may reflect limited data on lower-tier programs.');
    }
    return sentences.join(' ');
  };

      const getSweatScoreForGame = (game, sport) => {
    if(!game) return null;
    try {
      const key = game.id || (game.away_team + game.home_team);
      if(sweatScores[key]) return sweatScores[key];
      const score = calcGameSweatScore(game, sport, fanmatchData, mlbGameContext, nbaTeamData);
      if(score) setSweatScores(prev => ({...prev, [key]: score}));
      return score;
    } catch(e) {
      console.log('SweatScore error:', e.message, game?.away_team);
      return null;
    }
  };

  const fuzzyMatch = (a, b) => {
    if(!a || !b) return 0;
    const s1 = a.toLowerCase().trim();
    const s2 = b.toLowerCase().trim();
    if(s1 === s2) return 1;
    if(s1.includes(s2) || s2.includes(s1)) return 0.9;
    const words1 = s1.split(' ');
    const words2 = s2.split(' ');
    const matches = words1.filter(w => words2.some(w2 => w2.includes(w) || w.includes(w2)));
    return matches.length / Math.max(words1.length, words2.length);
  };
  const fetchParlayAnalysis = async () => {
  if(parlayLegs.length < 2) return;
  setParlayAnalysis('');
  setParlayAnalysisVisible(true);
  setParlayAnalysisLoading(true);
  try {
    // Build context for each leg from pipeline data
    const buildLegContext = (leg) => {
      const mlbCtx = Object.values(mlbGameContext).find((ctx: any) =>
        leg.matchup?.includes(ctx.home_team?.split(' ').pop()) ||
        leg.matchup?.includes(ctx.away_team?.split(' ').pop())
      ) as any;
      if(mlbCtx) {
        return `MLB Pipeline: ${mlbCtx.home_pitcher || 'TBD'} xERA ${mlbCtx.home_sp_xera || 'N/A'} vs ${mlbCtx.away_pitcher || 'TBD'} xERA ${mlbCtx.away_sp_xera || 'N/A'}. K gap: home ${mlbCtx.home_k_gap || 'N/A'}, away ${mlbCtx.away_k_gap || 'N/A'}. wRC+: ${mlbCtx.home_wrc_plus || 'N/A'} vs ${mlbCtx.away_wrc_plus || 'N/A'}. Park: ${mlbCtx.park_run_factor || 'N/A'}. Weather: ${mlbCtx.temperature || '?'}°F, ${mlbCtx.wind_speed || 0}mph ${mlbCtx.wind_direction || ''}. NRFI score: ${mlbCtx.nrfi_score || 'N/A'}. Total lean: ${mlbCtx.over_lean === true ? 'OVER' : mlbCtx.over_lean === false ? 'UNDER' : 'NEUTRAL'}. Spread lean: ${mlbCtx.spread_lean === 'home' ? mlbCtx.home_team + ' favored' : mlbCtx.spread_lean === 'away' ? mlbCtx.away_team + ' favored' : 'neutral'}. Spread delta: ${mlbCtx.spread_delta != null ? mlbCtx.spread_delta.toFixed(1) + ' runs' : 'N/A'}.`;
      }
      const homeNBA = Object.values(nbaTeamData).find((t: any) =>
        leg.matchup?.includes(t.team?.split(' ').pop())
      ) as any;
      if(homeNBA) {
        return `NBA Pipeline: Net ${homeNBA.net_rating > 0 ? '+' : ''}${homeNBA.net_rating?.toFixed(1)}, DefRtg ${homeNBA.defensive_rating?.toFixed(1)}, eFG% ${homeNBA.efg_pct?.toFixed(1)}, Pace ${homeNBA.pace?.toFixed(1)}, Home ${homeNBA.home_record || 'N/A'}, Away ${homeNBA.away_record || 'N/A'}, Last5 Net ${homeNBA.last_10_net_rating?.toFixed(1) || 'N/A'}${homeNBA.injury_note ? ', Injuries: ' + homeNBA.injury_note : ''}.`;
      }
      return '';
    };

    // Detect correlated legs
    const matchupMap = {};
    parlayLegs.forEach((l, i) => {
      const key = l.matchup?.toLowerCase().replace(/\s+/g, '') || '';
      if(!matchupMap[key]) matchupMap[key] = [];
      matchupMap[key].push(i + 1);
    });
    const correlatedPairs = Object.values(matchupMap).filter(arr => arr.length > 1);
    const hasCorrelation = correlatedPairs.length > 0;

    const legsWithContext = parlayLegs.map((l, i) => {
      const ctx = buildLegContext(l);
      return `Leg ${i+1}: ${l.pick} (${l.matchup}) at ${l.oddsSign}${l.odds}${ctx ? '\n  → ' + ctx : ''}`;
    }).join('\n');

    const correlationNote = hasCorrelation
      ? `\n\nCORRELATION ALERT: Legs ${correlatedPairs.map(p => p.join(' & ')).join(', ')} are from the same game — flag as HIGH correlation.\n`
      : '';

    const prompt = `You are Jerry, sharp AI analyst for The Sweat Locker sports betting app.

Parlay legs with pipeline data:
${legsWithContext}
Combined odds: ${parlayAmerican}
Implied probability: ${parlayProb}%
Total legs: ${parlayLegs.length}
${correlationNote}
Search the web for current injury reports, recent form, and line movement for each team or player. Combine web findings with the pipeline data above. Do NOT write any preamble — go straight to JSON output after searching.

Return ONLY a JSON object:
{
  "legs": [
    {
      "leg": 1,
      "pick": "exact pick text",
      "grade": "A",
      "gradeColor": "#00e5a0",
      "confidence": 85,
      "jerry": "One sharp sentence — reference specific pipeline data or web findings that justify the grade.",
      "risk": "One specific risk factor",
      "correlation": "NONE",
      "pipelineData": true
    }
  ],
  "overallGrade": "B+",
  "overallColor": "#FFB800",
  "verdict": "One sharp Jerry verdict — is the juice worth the squeeze?",
  "strongestLeg": 1,
  "weakestLeg": 2,
  "hasCorrelation": false
}

CORRELATION CHECK per leg:
- "HIGH" if multiple legs from the same game
- "MODERATE" if OVER total + team ML from same game, or two MLB unders from same division
- "NONE" if no correlation detected

NRFI LEG RULES:
- If a leg says 'NRFI' — grade based on NRFI score in pipeline data
- NRFI score >= 75: Grade A. 65-74: Grade B. 55-64: Grade C. < 55: Grade D
- Always reference both pitcher xERA values when grading NRFI legs

Grade scale:
A = Strong edge, pipeline data confirms, line movement supports
B = Solid play, good value, pipeline data mostly supports
C = Playable but risky, pipeline data mixed or missing
D = Weak leg, pipeline data against or significant concerns
F = Avoid — injury, bad line, pipeline data conflicts

gradeColor: A=#00e5a0, B=#FFB800, C=#0099ff, D=#ff8c00, F=#ff4d6d
Never say "bet" or "must play". Be sharp and direct.
CRITICAL: Your entire response must be valid JSON starting with { and ending with }. No text before or after.`;

    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true'
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 2000,
        tools: [{type: 'web_search_20250305', name: 'web_search'}],
        messages: [{role: 'user', content: prompt}]
      })
    });
    const data = await response.json();
    const text = data?.content?.filter(b => b.type === 'text').map(b => b.text).join('') || '';
    try {
let parsed = null;
try {
  const clean = text.replace(/```json|```/g, '').trim();
  parsed = JSON.parse(clean);
} catch(e1) {
  try {
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if(jsonMatch) parsed = JSON.parse(jsonMatch[0]);
  } catch(e2) {
    try {
      const start = text.indexOf('{');
      const end = text.lastIndexOf('}');
      if(start !== -1 && end !== -1) parsed = JSON.parse(text.substring(start, end + 1));
    } catch(e3) { parsed = null; }
  }
}
if(parsed) {
  // Ensure hasCorrelation is set
  if(parsed.hasCorrelation === undefined) {
    parsed.hasCorrelation = hasCorrelation || (parsed.legs || []).some(l => l.correlation === 'HIGH' || l.correlation === 'MODERATE');
  }
  setParlayAnalysis(parsed);
} else {
  const verdictMatch = text.match(/"verdict":\s*"([^"]+)"/);
  setParlayAnalysis({error: verdictMatch ? verdictMatch[1] : text.replace(/[{}"[\]]/g, '').substring(0, 300)});
}
    } catch(e) {
      setParlayAnalysis({error: text});
    }
  } catch(e) {
    setParlayAnalysis({error: "Jerry couldn't break this one down. Check your legs manually."});
  }
  setParlayAnalysisLoading(false);
};

  const fetchPickRecap = async (bet, result) => {
    try {
      const wins = bets.filter(b=>b.result==='Win').length + (result==='Win'?1:0);
      const losses = bets.filter(b=>b.result==='Loss').length + (result==='Loss'?1:0);
      const prompt = `You are Jerry, sharp AI analyst for The Sweat Locker. One sentence only — no more.

Bet: ${bet.pick} (${bet.sport})
Result: ${result}
Season record after this: ${wins}-${losses}

Write one punchy Jerry reaction to this result. If Win — celebrate sharply. If Loss — stay composed and confident. Reference the pick specifically. End with 🔒 if Win, no emoji if Loss. No disclaimers. Just Jerry being Jerry.`;

      const response = await fetch('https://api.anthropic.com/v1/messages',{
        method:'POST',
        headers:{
          'Content-Type':'application/json',
          'x-api-key':ANTHROPIC_API_KEY,
          'anthropic-version':'2023-06-01',
          'anthropic-dangerous-direct-browser-access':'true'
        },
        body:JSON.stringify({model:'claude-haiku-4-5-20251001',max_tokens:1000,messages:[{role:'user',content:prompt}]})
      });
      const data = await response.json();
      const text = data?.content?.[0]?.text || '';
      if(text) { setPickRecap(text); setPickRecapVisible(true); }
    } catch(e) {
      console.log('Pick recap error:', e.message);
    }
  };

  const calcAndPatchMLBContext = async (contextData: any[]) => {
  for (const game of contextData) {
    if (game.projected_total !== null && game.over_lean !== null) continue;

    const homeRPG  = game.home_runs_per_game ?? 4.5;
    const awayRPG  = game.away_runs_per_game ?? 4.5;
    const parkFactor = (game.park_run_factor ?? 100) / 100;
    const temp     = game.temperature ?? 72;
    const windMph  = game.wind_speed   ?? 0;
    const windDir  = (game.wind_direction ?? '').toLowerCase();
    const precip   = game.precipitation ?? 0;

    let projected = (homeRPG + awayRPG) * parkFactor;

    if (!game.dome_game) {
      if      (temp < 50)  projected -= 0.8;
      else if (temp < 60)  projected -= 0.4;
      else if (temp > 85)  projected += 0.3;

      const blowingOut = windMph > 10 && (windDir.includes('out') || ['sw','s','se'].includes(windDir));
      const blowingIn  = windMph > 10 && (windDir.includes('in')  || ['ne','n','nw','e','w'].includes(windDir));
      if      (blowingOut) projected += windMph > 15 ? 0.8 : 0.4;
      else if (blowingIn)  projected -= windMph > 15 ? 0.8 : 0.4;

      if (precip > 0) projected -= 0.3;
    }

    const homeERA = game.home_pitcher_home_era;
    const awayERA = game.away_pitcher_away_era;
    if (homeERA != null) {
      if      (homeERA < 3.0)  projected -= 0.5;
      else if (homeERA < 3.75) projected -= 0.25;
      else if (homeERA > 5.0)  projected += 0.3;
    }
    if (awayERA != null) {
      if      (awayERA < 3.0)  projected -= 0.5;
      else if (awayERA < 3.75) projected -= 0.25;
      else if (awayERA > 5.0)  projected += 0.3;
    }

    projected = Math.round(projected * 10) / 10;

    const delta   = projected - 8.8;
    const overLean = delta > 0.3 ? true : delta < -0.3 ? false : null;

    const confidence =
      (game.home_runs_per_game != null && game.away_runs_per_game != null && homeERA != null && awayERA != null && game.temperature != null) ? 'HIGH' :
      (game.home_runs_per_game != null && game.away_runs_per_game != null && game.temperature != null) ? 'MEDIUM' :
      (game.home_runs_per_game != null && game.away_runs_per_game != null) ? 'LOW' : 'VERY LOW';

    const { error } = await supabase
      .from('mlb_game_context')
      .update({ projected_total: projected, over_lean: overLean, confidence })
      .eq('id', game.id);

    if (error) console.error('[calcAndPatchMLBContext]', game.game_id, error.message);
  }
};
  
  const fetchMLBGameContext = async () => {
  try {
    // Use ET date to match pipeline's game_date
    const etStr = new Date().toLocaleDateString('en-CA', {timeZone: 'America/New_York'});
    const result = await supabase
      .from('mlb_game_context')
      .select('*')
      .eq('game_date', etStr)
      .limit(30);
    let data = result?.data;
    // If no games for today (pipeline hasn't run yet), fall back to most recent
    if(!data || data.length === 0) {
      const fallback = await supabase
        .from('mlb_game_context')
        .select('*')
        .order('game_date', {ascending: false})
        .limit(30);
      data = fallback?.data;
      // Only use if from the last 2 days — dedupe by keeping newest per team
      if(data && data.length > 0) {
        const seen = {};
        data = data.filter(g => {
          const key = g.home_team + '_' + g.away_team;
          if(seen[key]) return false;
          seen[key] = true;
          return true;
        });
      }
    }
    if(data && data.length > 0) {
      const contextMap = {};
      data.forEach(game => {
        contextMap[game.home_team] = game;
        contextMap[game.away_team] = game;
        if(game.game_id) contextMap[game.game_id] = game;
        const addAliases = (team: string) => {
          const aliases = MLB_TEAM_ALIASES[team];
          if(aliases) aliases.forEach(a => { contextMap[a] = game; });
        };
        addAliases(game.home_team);
        addAliases(game.away_team);
      });
      setMlbGameContext(contextMap);
    }
  } catch(e) {
    //console.log('MLB context fetch error:', e.message);
  }
};

const fetchNBATeamContext = async () => {
  try {
    const result = await supabase
      .from('nba_team_stats')
      .select('*')
      .eq('season', '2025-26');
    const data = result?.data;
    if(data && data.length > 0) {
      const contextMap = {};
      data.forEach(team => {
        contextMap[team.team] = team;
      });
      setNbaTeamData(contextMap);
      //console.log('NBA team stats loaded:', data.length, 'teams');
    }
  } catch(e) {
    console.log('NBA team stats fetch error:', e.message);
  }
};

const fetchNBAInjuries = async () => {
  try {
    const { data } = await supabase
      .from('nba_injuries')
      .select('*');
    if(data && data.length > 0) {
      const injuryMap: Record<string, any[]> = {};
      data.forEach(inj => {
        const team = inj.team_name;
        if(!team) return;
        if(!injuryMap[team]) injuryMap[team] = [];
        injuryMap[team].push(inj);
      });
      setNbaInjuryData(injuryMap);
      //console.log('NBA injuries loaded:', data.length, 'players');
    }
  } catch(e) {
    //console.log('NBA injury fetch error:', e);
  }
};

const fetchPlayoffSeries = async () => {
  try {
    const today = new Date();
    const playoffStart = new Date('2026-04-19');
    if(today < playoffStart) {
      setIsPlayoffMode(false);
      return;
    }
    setIsPlayoffMode(true);
    const { data } = await supabase
      .from('nba_playoff_series')
      .select('*')
      .eq('season', 2024);
    if(data && data.length > 0) {
      const seriesMap = {};
      data.forEach(s => {
        seriesMap[s.home_team] = s;
        seriesMap[s.away_team] = s;
      });
      setPlayoffSeries(seriesMap);
    }
  } catch(e) {}
};

const fetchJerryRecord = async () => {
  setJerryRecordLoading(true);
  try {
    // Prop Jerry A-grade results only
    const { data: propGrades } = await supabase
      .from('prop_grades')
      .select('*')
      .eq('grade', 'A')
      .order('created_at', {ascending: false})
      .limit(100);

    const allAGrades = propGrades || [];
    const resolvedProps = allAGrades.filter(p => p.result === 'Win' || p.result === 'Loss');
    const propWins = resolvedProps.filter(p => p.result === 'Win').length;
    const propLosses = resolvedProps.filter(p => p.result === 'Loss').length;
    const pendingProps = allAGrades.filter(p => p.result === 'Pending').length;

    // Break down by sport
    const propBySport = {};
    resolvedProps.forEach(p => {
      const s = p.sport || 'Unknown';
      if(!propBySport[s]) propBySport[s] = {wins:0, losses:0};
      if(p.result === 'Win') propBySport[s].wins++;
      else propBySport[s].losses++;
    });

    // Recent A-grade picks (resolved + pending)
    const recentProps = allAGrades.slice(0, 10);

    // NRFI Model record from mlb_game_results
    // Track ONLY tiers where Jerry calls a NRFI lean in his reads:
    //   70-79 = mild lean, 90-94 = PRIME tier
    // Exclude 80-89 (neutral tier, no lean) and 95+ (volatile trap, Jerry flags
    // volatility not a lean). Keeps record aligned with what Jerry actually said.
    let nrfi = {wins:0, losses:0};
    try {
      const { data: nrfiData } = await supabase
        .from('mlb_game_results')
        .select('nrfi_score, nrfi_result')
        .not('nrfi_result', 'is', null);
      if(nrfiData && nrfiData.length > 0) {
        const leanTierGames = nrfiData.filter((r: any) =>
          (r.nrfi_score >= 70 && r.nrfi_score <= 79) ||
          (r.nrfi_score >= 90 && r.nrfi_score <= 94)
        );
        nrfi.wins = leanTierGames.filter((r: any) => r.nrfi_result === 'NRFI').length;
        nrfi.losses = leanTierGames.filter((r: any) => r.nrfi_result === 'YRFI').length;
      }
    } catch(e) {}

    // Best Bet history from dedicated table
    const { data: bestBetHistory } = await supabase
      .from('daily_best_bet_history')
      .select('*')
      .order('bet_date', {ascending: false})
      .limit(14);

    setJerryRecord({
      props: { wins: propWins, losses: propLosses, pending: pendingProps, bySport: propBySport, recent: recentProps },
      nrfi,
      bestBets: bestBetHistory || [],
    });
  } catch(e) {
    console.log('Jerry record error:', e?.message);
  }
  setJerryRecordLoading(false);
};

  const fetchModelEdgeGames = async (sport = 'NCAAB') => {
  setModelEdgeLoading(true);
  try {
    const r = await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/odds', {
      params: {
        apiKey: ODDS_API_KEY,
        regions: 'us,us2',
        markets: 'spreads,totals,h2h',
        oddsFormat: 'american',
        bookmakers: 'hardrockbet,draftkings,fanduel,espnbet,betmgm,caesars'
      }
    });
    const mapped = (r.data||[]).map(g => ({
      ...g,
      away_team: sport==='NCAAB' ? stripMascot(g.away_team) : g.away_team,
      home_team: sport==='NCAAB' ? stripMascot(g.home_team) : g.home_team,
    }));
    setModelEdgeData(mapped);
  } catch(e) { setModelEdgeData([]); }
  setModelEdgeLoading(false);
};

const fetchDailyBestBet = async () => {
  const CACHE_KEY = 'sweatlocker_daily_best_bet_v4';
  const _now = new Date();
  const today = _now.getFullYear() + '-' + String(_now.getMonth()+1).padStart(2,'0') + '-' + String(_now.getDate()).padStart(2,'0');
  const etHour = parseInt(new Date().toLocaleTimeString('en-US', {timeZone:'America/New_York', hour:'numeric', hour12:false}));

  // Play of the Day is now computed server-side by play_of_day.py
  // App just reads from jerry_cache and generates Jerry narrative if missing
  try {
    const { data: supabaseCache } = await supabase
      .from('jerry_cache')
      .select('data, fetched_at')
      .eq('game_id', `best_bet_${today}`)
      .single();

    if(supabaseCache?.data) {
      if(supabaseCache.data.noGames) {
        setDailyBestBet({noGames: true});
        return;
      }

      // Pipeline generated the pick — use it
      if(supabaseCache.data.pipelineGenerated) {
        // Generate Jerry narrative if not already present
        if(!supabaseCache.data.narrative && supabaseCache.data.game) {
          try {
            const ctx = supabaseCache.data.context || {};
            const gameStr = `${supabaseCache.data.game.away_team} @ ${supabaseCache.data.game.home_team}`;
            const aiResp = await fetch('https://api.anthropic.com/v1/messages', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'anthropic-dangerous-direct-browser-access': 'true'
              },
              body: JSON.stringify({
                model: 'claude-haiku-4-5-20251001',
                max_tokens: 200,
                ...(supabaseCache.data.sport === 'NBA' || supabaseCache.data.sport === 'NHL' ? {tools:[{type:'web_search_20250305',name:'web_search'}]} : {}),
                messages: [{
                  role: 'user',
                  content: `CRITICAL: TODAY'S DATE IS ${new Date().toLocaleDateString('en-US', {timeZone: 'America/New_York', year: 'numeric', month: 'long', day: 'numeric', weekday: 'long'})}. Use this date, not your internal date. You are Jerry, a sharp sports analyst for The Sweat Locker app. You HAVE all the data you need below — do NOT say you lack data, do NOT offer to verify, do NOT hedge. Write with full confidence using the specific numbers provided. Sport is ${supabaseCache.data.sport} — only reference metrics relevant to that sport.

This is today's Play of the Day — the single best play across all sports, selected by the Sweat Locker pipeline.

Game: ${gameStr}
Sport: ${supabaseCache.data.sport}
Play: ${supabaseCache.data.leanDisplay || 'Model Edge'}
Sweat Score: ${supabaseCache.data.score?.total || 'N/A'}/100
${supabaseCache.data.sport === 'MLB' ? `
${supabaseCache.data.score?.isNRFI ? `PLAY TYPE: NRFI (No Run First Inning)
NRFI Score: ${supabaseCache.data.score.nrfiScore}/100` : ''}
${ctx.home_pitcher ? `Home pitcher: ${ctx.home_pitcher} xERA ${ctx.home_sp_xera || 'N/A'}` : ''}
${ctx.away_pitcher ? `Away pitcher: ${ctx.away_pitcher} xERA ${ctx.away_sp_xera || 'N/A'}` : ''}
${ctx.projected_total ? `Projected total: ${ctx.projected_total}` : ''}
${ctx.spread_delta != null ? `Spread delta: ${ctx.spread_delta} runs vs market` : ''}
${ctx.venue ? `Venue: ${ctx.venue} | Temp: ${ctx.temperature}°F` : ''}
${ctx.nrfi_score ? `NRFI score: ${ctx.nrfi_score}` : ''}` : ''}
${supabaseCache.data.sport === 'NBA' ? `
This is an NBA play — reference net rating gap, defensive rating, pace, eFG%, home/away records, injuries, and playoff context if applicable. Do NOT mention xERA, NRFI, pitchers, or baseball stats. Web search for tonight's injury report and lineup news if needed.` : ''}
${supabaseCache.data.sport === 'NHL' ? `
This is an NHL play — reference goalie matchup, special teams, pace. Do NOT mention baseball or basketball stats.` : ''}

Rules:
- Write 2-3 sentences MAX explaining the edge
- Reference specific data points for the correct sport
- End with the specific play ("That's the play", "Model sits there", etc.)
- NEVER say "bet", "must play", "I don't have data", "let me verify"
- NEVER mix sports (no xERA for NBA games, no net rating for MLB games)
- For NBA: if data is missing, use web search for tonight's news — do NOT say "no data available"
- Sound like a sharp friend who already did the homework`
                }]
              })
            });
            const aiData = await aiResp.json();
            const narrative = aiData?.content?.filter(b => b.type === 'text').map(b => b.text).join('') || '';
            supabaseCache.data.narrative = narrative;
            // Update cache with narrative
            try {
              await supabase.from('jerry_cache').upsert({
                game_id: `best_bet_${today}`,
                sport: supabaseCache.data.sport || 'MLB',
                narrative: supabaseCache.data.narrative || '',
                cache_key: `best_bet_${today}`,
                data: supabaseCache.data,
                fetched_at: supabaseCache.fetched_at,
              }, { onConflict: 'game_id,sport' });
            } catch(e) {}
          } catch(e) {
            supabaseCache.data.narrative = "Jerry's analyzing this one. Check back shortly.";
          }
        }
        setDailyBestBet(supabaseCache.data);
        try { await AsyncStorage.setItem(CACHE_KEY, JSON.stringify({data:supabaseCache.data, timestamp:Date.now()})); } catch(e) {}
        return;
      }

      // Legacy client-side pick — serve it
      if(!supabaseCache.data.noGames) {
        setDailyBestBet(supabaseCache.data);
        try { await AsyncStorage.setItem(CACHE_KEY, JSON.stringify({data:supabaseCache.data, timestamp:Date.now()})); } catch(e) {}
        return;
      }
    }
  } catch(e) {}

  // No pipeline pick yet — show waiting message before 10am, try local cache after
  if(etHour < 10) {
    setDailyBestBet({noGames: false, waiting: true});
    return;
  }

  // Check local cache
  try {
    const cached = await AsyncStorage.getItem(CACHE_KEY);
    if(cached) {
      const parsed = JSON.parse(cached);
      const cacheDate = new Date(parsed.timestamp);
      const cacheDateStr = cacheDate.getFullYear() + '-' + String(cacheDate.getMonth()+1).padStart(2,'0') + '-' + String(cacheDate.getDate()).padStart(2,'0');
      if(cacheDateStr === today && parsed.data && !parsed.data.noGames) {
        setDailyBestBet(parsed.data);
        return;
      }
    }
  } catch(e) {}

  // No pipeline pick found — show waiting message
  // Client-side scanning removed — Play of the Day is computed by play_of_day.py in the pipeline
  console.log('[BestBet] No pipeline pick found for today — showing waiting');
  setDailyBestBet({noGames: false, waiting: true});
  setDailyBestBetLoading(false);
  return;

  // ── LEGACY CLIENT-SIDE SCANNING BELOW — DISABLED ──
  setDailyBestBetLoading(true);

  try {
    const todayStart = new Date(); todayStart.setHours(0,0,0,0);
    const todayEnd = new Date(); todayEnd.setHours(23,59,59,999);
    const now = new Date();

    let bestGame = null;
    let bestScore = 0;
    let bestSport = null;
    let bestScoreObj = null;

    // Scan MLB games
    try {
      const mlbResp = await axios.get('https://statsapi.mlb.com/api/v1/schedule', {
        params: { sportId: 1, date: today, hydrate: 'probablePitcher' }
      });
      const mlbOddsResp = await axios.get('https://api.the-odds-api.com/v4/sports/baseball_mlb/odds', {
        params: {
          apiKey: ODDS_API_KEY,
          regions: 'us,us2',
          markets: 'spreads,totals,h2h',
          oddsFormat: 'american',
          bookmakers: 'hardrockbet,draftkings,fanduel,betmgm,caesars'
        }
      });

      const mlbGames = [];
      for(const dateEntry of mlbResp.data?.dates || []) {
        for(const game of dateEntry.games || []) {
          if(game.status?.abstractGameState === 'Final') continue;
          const gameTime = new Date(game.gameDate);
          if(gameTime > todayEnd) continue;
          // Only skip games that started 4+ hours ago (likely over)
          if(gameTime < new Date(now.getTime() - 4*60*60*1000)) continue;
          const oddsGame = (mlbOddsResp.data || []).find((og: any) => {
            return og.home_team.includes(game.teams?.home?.team?.name?.split(' ').pop() || '') ||
                   game.teams?.home?.team?.name?.includes(og.home_team.split(' ').pop() || '');
          });
          if(oddsGame) {
            mlbGames.push({
              ...oddsGame,
              home_team: game.teams?.home?.team?.name || oddsGame.home_team,
              away_team: game.teams?.away?.team?.name || oddsGame.away_team,
              commence_time: game.gameDate,
            });
          }
        }
      }

      console.log('[BestBet] MLB games found:', mlbGames.length, mlbGames.map(g => g.home_team).slice(0,3));
      let bestNRFIScore = 0;
      let bestNRFIGame = null;
      let bestNRFICtx = null;

      for(const game of mlbGames) {
        // Use pre-fetched mlbGameContext instead of individual Supabase queries
        const mlbCtx = mlbGameContext[game.home_team] || mlbGameContext[game.away_team] ||
          Object.values(mlbGameContext).find((ctx: any) =>
            ctx.home_team === game.home_team || ctx.away_team === game.away_team
          ) as any;
        const mlbCtxMap = mlbCtx ? { [game.home_team]: mlbCtx } : {};
        const scoreObj = calcGameSweatScore(game, 'MLB', fanmatchData, mlbCtxMap, nbaTeamData);
        if(scoreObj && scoreObj.total > bestScore) {
          bestScore = scoreObj.total;
          bestGame = game;
          bestSport = 'MLB';
          bestScoreObj = scoreObj;
        }
        // Track best NRFI candidate separately
        if(mlbCtx?.nrfi_score && mlbCtx.nrfi_score > bestNRFIScore) {
          bestNRFIScore = mlbCtx.nrfi_score;
          bestNRFIGame = game;
          bestNRFICtx = mlbCtx;
        }
      }
      console.log('[BestBet] MLB scan done — bestScore:', bestScore, 'bestNRFI:', bestNRFIScore, 'mlbGameContext keys:', Object.keys(mlbGameContext).length);

      // NRFI is our flagship model — prioritize it over generic Sweat Scores
      // NRFI 75+ always wins unless another game has a truly elite Sweat Score (80+)
      if(bestNRFIScore >= 75 && bestNRFIGame && (bestScore < 80 || bestNRFIScore >= bestScore)) {
        bestGame = bestNRFIGame;
        bestSport = 'MLB';
        bestScoreObj = {
          ...calcGameSweatScore(bestNRFIGame, 'MLB', fanmatchData, {[bestNRFIGame.home_team]: bestNRFICtx}, nbaTeamData),
          isNRFI: true,
          nrfiScore: bestNRFIScore,
          leanSide: 'NRFI',
          leanBet: 'No Run First Inning',
        };
        bestScore = bestNRFIScore;
      }
    } catch(e) {}

    // Scan NBA games
    try {
      const nbaResp = await axios.get('https://api.the-odds-api.com/v4/sports/basketball_nba/odds', {
        params: {
          apiKey: ODDS_API_KEY,
          regions: 'us,us2',
          markets: 'spreads,totals,h2h',
          oddsFormat: 'american',
          bookmakers: 'hardrockbet,draftkings,fanduel,betmgm,caesars'
        }
      });

      const nbaGames = (nbaResp.data || []).filter((g: any) => {
        const t = new Date(g.commence_time);
        // Include today's games that haven't been over for 4+ hours
        return t >= todayStart && t <= todayEnd && t > new Date(now.getTime() - 4*60*60*1000);
      });

      for(const game of nbaGames) {
        const scoreObj = calcGameSweatScore(game, 'NBA', fanmatchData, null, nbaTeamData);
        // Don't let NBA overwrite a strong NRFI pick unless NBA score is truly elite (80+)
        if(scoreObj && scoreObj.total > bestScore && (!bestScoreObj?.isNRFI || scoreObj.total >= 80)) {
          bestScore = scoreObj.total;
          bestGame = game;
          bestSport = 'NBA';
          bestScoreObj = scoreObj;
        }
      }
    } catch(e) {}
   
// Scan UFC — only on fight days
    try {
      const ufcResp = await axios.get('https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds', {
        params: {
          apiKey: ODDS_API_KEY,
          regions: 'us,us2',
          markets: 'h2h',
          oddsFormat: 'american',
          bookmakers: 'hardrockbet,draftkings,fanduel,betmgm,caesars'
        }
      });

      const ufcEvents = (ufcResp.data || []).filter((g: any) => {
        const t = new Date(g.commence_time);
        return t >= todayStart && t <= todayEnd && t > now;
      });

      // Only consider main card fights — filter for top bouts by odds volume
      const topUFCFights = ufcEvents
        .filter((g: any) => (g.bookmakers || []).length >= 3)
        .slice(0, 5);

      for(const fight of topUFCFights) {
        const scoreObj = calcGameSweatScore(fight, 'UFC', fanmatchData, null, nbaTeamData);
        if(scoreObj && scoreObj.total > bestScore) {
          bestScore = scoreObj.total;
          bestGame = fight;
          bestSport = 'UFC';
          bestScoreObj = scoreObj;
        }
      }
    } catch(e) {}

    console.log('[BestBet] Final — bestScore:', bestScore, 'bestSport:', bestSport, 'bestGame:', bestGame?.home_team);
    if(!bestGame || !bestScoreObj) {
      console.log('[BestBet] No best game found — showing noGames');
      setDailyBestBet({noGames: true});
      setDailyBestBetLoading(false);
      return;
    }

    // Generate Jerry narrative for best bet
    const aiResp = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 200,
        messages: [{
          role: 'user',
          content: `You are Jerry, a sharp sports betting analyst. This is today's single best bet across all sports.

Game: ${bestGame.away_team} @ ${bestGame.home_team} (${bestSport})
${bestScoreObj.isNRFI ? `NRFI Score: ${bestScoreObj.nrfiScore}/100 — No Run First Inning play
Home pitcher: ${bestNRFICtx?.home_pitcher} xERA ${bestNRFICtx?.home_sp_xera}
Away pitcher: ${bestNRFICtx?.away_pitcher} xERA ${bestNRFICtx?.away_sp_xera}
Temperature: ${bestNRFICtx?.temperature}°F | Wind: ${bestNRFICtx?.wind_speed}mph ${bestNRFICtx?.wind_direction}
Park factor: ${bestNRFICtx?.park_run_factor}
Home wRC+: ${bestNRFICtx?.home_wrc_plus} | Away wRC+: ${bestNRFICtx?.away_wrc_plus}` : 
`Sweat Score: ${bestScoreObj.total}/100
Lean: ${bestScoreObj.leanSide || 'unclear'}
Key factors: ${bestScoreObj.efgMismatch || bestScoreObj.injuryContext || 'model edge detected'}`}

${bestScoreObj.isNRFI ? 
'This is a NRFI (No Run First Inning) play. Explain WHY both pitchers are expected to have clean first innings based on the xERA values, weather, and park. End with "NRFI is the play." Never say "bet" or "must play".' :
'Give a 2-3 sentence take on WHY this is today\'s best bet. Reference the specific data. End with the specific side to back. Never say "bet" or "must play".'} Sound like a sharp friend.`
        }]
      })
    });

    const aiData = await aiResp.json();
    const narrative = aiData?.content?.[0]?.text || 'Top model edge of the day.';

    const bestBetData = {
      game: bestGame,
      sport: bestSport,
      score: bestScoreObj,
      narrative,
      generatedAt: today,
      leanDisplay: bestScoreObj.isNRFI
        ? `NRFI — Score ${bestScoreObj.nrfiScore}/100`
        : typeof bestScoreObj?.leanSide === 'string'
        ? bestScoreObj.leanSide
        : bestScoreObj?.totalBet?.pick
        ? bestScoreObj.totalBet.pick
        : bestSport === 'NBA'
        ? stripMascot(bestGame?.home_team || '')
        : bestSport === 'MLB'
        ? `${stripMascot(bestGame?.home_team || '')} vs ${stripMascot(bestGame?.away_team || '')}`
        : 'Model Edge',
    };

    // Save to Supabase — shared across all users for the day
    try {
      await supabase.from('jerry_cache').upsert({
        cache_key: `best_bet_${today}`,
        data: bestBetData,
        fetched_at: new Date().toISOString(),
      }, {onConflict: 'cache_key'});
    } catch(e) {}

    // Log to best bet history for Jerry's Track Record
    try {
      await supabase.from('daily_best_bet_history').upsert({
        bet_date: today,
        sport: bestSport,
        game: `${bestGame.away_team} @ ${bestGame.home_team}`,
        lean: bestScoreObj.isNRFI ? 'NRFI' : (bestScoreObj.leanSide || 'Model Edge'),
        sweat_score: bestScoreObj.isNRFI ? bestScoreObj.nrfiScore : bestScoreObj.total,
        narrative,
        result: 'Pending',
      }, {onConflict: 'bet_date'});
    } catch(e) {}

    // Save to local cache
    try {
      await AsyncStorage.setItem(CACHE_KEY, JSON.stringify({
        data: bestBetData,
        timestamp: Date.now()
      }));
    } catch(e) {}

    setDailyBestBet(bestBetData);

  } catch(e) {
    //console.log('Daily best bet error:', e);
    setDailyBestBet({noGames: true});
  }

  setDailyBestBetLoading(false);
};
  const fetchDailyBriefing = async () => {
    try {
      const cached = await AsyncStorage.getItem('sweatlocker_briefing_cache');
      if(cached) {
        const parsed = JSON.parse(cached);
        const now = new Date();
        const cacheTime = new Date(parsed.timestamp);
        // Reset at 5am daily — always fresh for morning
        const fiveAMToday = new Date(now);
        fiveAMToday.setHours(5,0,0,0);
        const cacheIsFromToday = cacheTime >= fiveAMToday;
        const cacheIsYoung = (Date.now() - parsed.timestamp) < 20*60*1000;
        if(cacheIsYoung || (cacheIsFromToday && (Date.now() - parsed.timestamp) < 4*60*60*1000)) {
          setDailyBriefing(parsed.text);
          return;
        }
      }
    } catch(e) {}
    setDailyBriefingLoading(true);
    try {
      const today = new Date().toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric'});
      const wins = bets.filter(b=>b.result==='Win').length;
      const losses = bets.filter(b=>b.result==='Loss').length;
      const pending = bets.filter(b=>b.result==='Pending').length;
     const now = new Date();
      // Fetch games fresh for briefing — don't rely on gamesData state
let todayGames = '';
try {
  const sportsToCheck = ['basketball_ncaab', 'basketball_nba', 'baseball_mlb', 'icehockey_nhl', 'americanfootball_nfl', 'mma_mixed_martial_arts'];
const now2 = new Date();
const todayEnd = new Date(now2); todayEnd.setHours(23,59,59,999);

for(const sportKey of sportsToCheck) {
  try {
    const gamesResp = await axios.get(`https://api.the-odds-api.com/v4/sports/${sportKey}/odds`, {
      params: {
        apiKey: ODDS_API_KEY,
        regions: 'us',
        markets: 'h2h',
        oddsFormat: 'american',
        bookmakers: 'draftkings'
      }
    });
    const games = (gamesResp.data||[])
      .filter(g => new Date(g.commence_time) > now2 && new Date(g.commence_time) <= todayEnd)
      .slice(0, 5)
      .map(g => `${stripMascot(g.away_team)} vs ${stripMascot(g.home_team)}`);
   if(games.length > 0) {
      todayGames += (todayGames ? ', ' : '') + games.join(', ');
    }
  } catch(e) {}
}
} catch(e) {}

      const prompt = `You are Jerry, sharp AI analyst for The Sweat Locker. Confident, energetic, like a seasoned handicapper. Today is ${today}. User record: ${wins}-${losses}. Pending: ${pending}.

Today's slate: ${todayGames || 'no major games scheduled today'}.

Write exactly 3 sentences in plain conversational text — no markdown, no headers, no asterisks, no hashtags, no bold text. React to what's actually on the slate today. Sport-specific context:
- NCAAB/NBA: reference efficiency edges, line movement, back-to-backs
- MLB: reference pitching matchups, weather, park factors if relevant
- NHL: reference goalie matchups, pace, line movement
- UFC/MMA: reference fighter styles, finishing rates, sharp money
- If multiple sports are on the slate mention the best angle across all of them
- If no games are on the slate, give bettors useful advice about line shopping, bankroll management, or what to watch for this week. Never mention data limitations.
Do NOT give a specific bet or pick. End with — Jerry.`;

      const response = await fetch('https://api.anthropic.com/v1/messages',{
        method:'POST',
        headers:{
          'Content-Type':'application/json',
          'x-api-key':ANTHROPIC_API_KEY,
          'anthropic-version':'2023-06-01',
          'anthropic-dangerous-direct-browser-access':'true'
        },
        body:JSON.stringify({
  model:'claude-haiku-4-5-20251001',
  max_tokens:400,
  messages:[{role:'user',content:prompt}]
})
      });
      const data = await response.json();
      const text = data?.content?.filter(b=>b.type==='text').map(b=>b.text).join('') || '';
      setDailyBriefing(text);
      await AsyncStorage.setItem('sweatlocker_briefing_cache', JSON.stringify({text, timestamp:Date.now()}));
    } catch(e) {
      console.log('BRIEFING ERROR:', e.message);
      setDailyBriefing("Big slate today — Jerry's got his eyes on the board. Head to the games tab and let's find some edges. — Jerry");
    }
    setDailyBriefingLoading(false);
  };
  const fetchBDLPlayerStats = async (playerName) => {
    try {
      const cacheKey = `sweatlocker_bdl_${playerName.replace(/\s/g,'_')}`;
      const cached = await AsyncStorage.getItem(cacheKey);
      if(cached) {
        const parsed = JSON.parse(cached);
        const ageHrs = (Date.now() - parsed.timestamp) / 3600000;
        if(ageHrs < 12) return parsed.data;
      }
      const searchResp = await axios.get('https://api.balldontlie.io/v1/players', {
        headers: {'Authorization': BDL_API_KEY},
        params: {search: playerName, per_page: 1}
      });
      const player = searchResp.data?.data?.[0];
      if(!player) return null;
      const statsResp = await axios.get('https://api.balldontlie.io/v1/stats', {
        headers: {'Authorization': BDL_API_KEY},
        params: {player_ids: [player.id], per_page: 5, seasons: [2024]}
      });
      const games = statsResp.data?.data || [];
      if(!games.length) return null;
      const avg = (key) => (games.reduce((s,g) => s + (g[key]||0), 0) / games.length).toFixed(1);
      // Recency-weighted average — most recent game weighted highest
      const RECENCY_WEIGHTS = [1.0, 0.85, 0.70, 0.55, 0.40];
      const weightedAvg = (key: string) => {
        const sorted = [...games].sort((a: any,b: any) => new Date(b.game?.date||0).getTime() - new Date(a.game?.date||0).getTime());
        let totalWeight = 0, weightedSum = 0;
        sorted.forEach((g, i) => {
          const w = RECENCY_WEIGHTS[i] || 0.30;
          weightedSum += (g[key]||0) * w;
          totalWeight += w;
        });
        return totalWeight > 0 ? parseFloat((weightedSum / totalWeight).toFixed(1)) : 0;
      };
      const data = {
        name: `${player.first_name} ${player.last_name}`,
        team: player.team?.abbreviation || '',
        last5: {
          pts: avg('pts'),
          reb: avg('reb'),
          ast: avg('ast'),
          min: avg('min')
        },
        weighted: {
          pts: weightedAvg('pts'),
          reb: weightedAvg('reb'),
          ast: weightedAvg('ast'),
        },
        rawGames: games.map(g => ({pts: g.pts, reb: g.reb, ast: g.ast, opp: g.game?.home_team_id === player.team?.id ? g.game?.visitor_team_id : g.game?.home_team_id})),
      };
      await AsyncStorage.setItem(cacheKey, JSON.stringify({data, timestamp: Date.now()}));
      return data;
    } catch(e) {
      return null;
    }
  };
  const fetchMLBBatterStats = async (playerName: string) => {
    if(!playerName) return null;
    try {
      const cacheKey = `sweatlocker_mlb_batter_${playerName.replace(/\s/g,'_')}`;
      const cached = await AsyncStorage.getItem(cacheKey);
      if(cached) {
        const parsed = JSON.parse(cached);
        if(Date.now() - parsed.timestamp < 12*3600000) return parsed.data; // 12hr cache
      }
      // Strip accents for API search compatibility (Iván → Ivan)
      const searchName = playerName.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
      // Search MLB Stats API for player
      const searchResp = await axios.get('https://statsapi.mlb.com/api/v1/people/search', {
        params: {names: searchName, sportId: 1, active: true},
        timeout: 10000
      });
      const people = searchResp.data?.people;
      if(!people || !people.length) {
        console.log(`⚾ [BatterStats] ${playerName} → no person found in MLB API search`);
        return null;
      }
      const playerId = people[0].id;
      const position = people[0].primaryPosition?.abbreviation || '';
      // Get current season hitting stats
      const statsResp = await axios.get(`https://statsapi.mlb.com/api/v1/people/${playerId}/stats`, {
        params: {stats: 'season', group: 'hitting', season: 2026},
        timeout: 10000
      });
      const splits = statsResp.data?.stats;
      if(!splits || !splits[0]?.splits?.length) {
        console.log(`⚾ [BatterStats] ${playerName} (id ${playerId}) → no 2026 hitting splits`);
        return null;
      }
      const s = splits[0].splits[0].stat;
      const pa = parseInt(s.plateAppearances || 0);
      const ab = parseInt(s.atBats || 1);
      const hits = parseInt(s.hits || 0);
      const so = parseInt(s.strikeOuts || 0);
      const data = {
        name: playerName,
        ba: pa > 0 ? parseFloat(s.avg || '.000') : null,
        ops: pa > 0 ? parseFloat(s.ops || '.000') : null,
        pa: pa,
        hits: hits,
        k_rate: pa > 0 ? Math.round((so / pa) * 100) : null,
        hr: parseInt(s.homeRuns || 0),
        position: position,
        isBench: pa < 20, // fewer than 20 PA = likely bench/platoon player
      };
      await AsyncStorage.setItem(cacheKey, JSON.stringify({data, timestamp: Date.now()}));
      return data;
    } catch(e: any) {
      console.log(`⚾ [BatterStats] ${playerName} → error:`, e?.message || e);
      return null;
    }
  };

  const fetchMLBContext = async (game) => {
  try {
    const { data } = await supabase
      .from('mlb_game_context')
      .select('*')
      .eq('game_id', game.id)
      .single();
    if(data) return data;
    // Try matching by team names if game_id doesn't match
    const { data: data2 } = await supabase
      .from('mlb_game_context')
      .select('*')
      .eq('home_team', game.home_team)
      .eq('away_team', game.away_team)
      .single();
    return data2 || null;
  } catch(e) {
    return null;
  }
};

const fetchMLBPitchers = async (homePitcher, awayPitcher) => {
  if(!homePitcher && !awayPitcher) return null;
  try {
    const names = [homePitcher, awayPitcher].filter(Boolean);
    const { data } = await supabase
      .from('mlb_pitcher_stats')
      .select('*')
      .in('player_name', names);
    return data || [];
  } catch(e) {
    return [];
  }
};
  const fetchGameNarrative = async (game, scoreData) => {
  const score = typeof scoreData === 'object' ? (scoreData?.total || 50) : (scoreData || 50);
  const spreadEdge = scoreData?.spreadEdge || 0;
  const sosDelta = scoreData?.sosDelta || 0;
  const luckAdjustment = scoreData?.luckAdjustment || 0;
  const efgMismatch = scoreData?.efgMismatch || 0;
  const predictedSpread = scoreData?.predictedSpread || 0;
  const hasFanmatch = scoreData?.hasFanmatch;
const homeWP = scoreData?.homeWP;
const awayTeamData = scoreData?.awayTeamData || null;
const homeTeamData = scoreData?.homeTeamData || null;
const ncaabBreakdown = scoreData || null;
const sport = gamesSport || 'NBA';
const lineMoveDirection = scoreData?.lineMoveDirection || null;
const totalIsPrimary = scoreData?.totalIsPrimary || false;
const totalSignalBet = scoreData?.totalSignalBet || null;
const totalBoostVal = scoreData?.totalBoost || 0;
const lineMoveTeam = scoreData?.lineMoveTeam || null;
const lineMovePoints = scoreData?.lineMovePoints || 0;
const modelBestBet = totalIsPrimary && totalSignalBet
  ? `${totalSignalBet} ${postedTotal} (TOTAL IS PRIMARY PLAY — ${totalBoostVal >= 12 ? 'massive' : 'significant'} model edge)`
  : ncaabBreakdown?.bestBet || 
    (scoreData?.leanSide ? `${scoreData.leanSide}` : null);

// Extract posted total from game object early so modelContext can use it
const postedTotalRaw = game?.bookmakers?.[0]?.markets?.find(m=>m.key==='totals')?.outcomes?.[0];
const postedTotal = postedTotalRaw ? parseFloat(postedTotalRaw.point) : null;
const projTotal = ncaabBreakdown?.projectedTotal ? parseFloat(ncaabBreakdown.projectedTotal) : null;
const totalDelta = postedTotal && projTotal ? parseFloat((projTotal - postedTotal).toFixed(1)) : null;
const totalLeanLabel = totalDelta === null ? 'N/A' :
  totalDelta <= -4 ? `STRONG UNDER (model ${Math.abs(totalDelta)} pts below posted — heavy under lean)` :
  totalDelta <= -2 ? `UNDER lean (model ${Math.abs(totalDelta)} pts below posted)` :
  totalDelta >= 4  ? `STRONG OVER (model ${Math.abs(totalDelta)} pts above posted — heavy over lean)` :
  totalDelta >= 2  ? `OVER lean (model ${totalDelta} pts above posted)` :
  `NEUTRAL (model within ${Math.abs(totalDelta)} pts of posted — no strong lean)`;

const awayName = game.away_team.split(' ').pop();
const homeName = game.home_team.split(' ').pop();
const conf = awayTeamData?.conf || homeTeamData?.conf || '';
// Detect NCAA Tournament round from seeds
const awaySeed = awayTeamData?.seed || 0;
const homeSeed = homeTeamData?.seed || 0;
const seedSum = awaySeed + homeSeed;
const isNCAATourney = awaySeed > 0 && homeSeed > 0;

const detectRound = () => {
  const now = new Date();
  const month = now.getMonth() + 1; // 1-indexed
  const day = now.getDate();
  
  // 2026 NCAA Tournament dates
  if(month === 3 && day <= 18) return 'R64'; // First Four
  if(month === 3 && day <= 20) return 'R64'; // First Round
  if(month === 3 && day <= 22) return 'R32'; // Second Round
  if(month === 3 && day <= 27) return 'S16'; // Sweet 16
  if(month === 3 && day <= 29) return 'E8';  // Elite 8
  if(month === 4 && day <= 5) return 'F4';   // Final Four
  if(month === 4 && day <= 7) return 'NCG';  // Championship
  return 'R64';
};

const NCAA_TRENDS = {
  R64: { favATS: 0.49, underPct: 0.51, note: 'R64 — chalk covers ~49%, slight over lean early' },
  R32: { favATS: 0.51, underPct: 0.53, note: 'R32 — tightening up, unders emerging' },
  S16: { favATS: 0.48, underPct: 0.55, note: 'Sweet 16 — elite defenses dominate, strong under lean' },
  E8:  { favATS: 0.50, underPct: 0.56, note: 'Elite 8 — grind games, unders hit 56%' },
  F4:  { favATS: 0.47, underPct: 0.58, note: 'Final Four — fade the favorite ATS, heavy under lean' },
  NCG: { favATS: 0.45, underPct: 0.60, note: 'Title game — favorites 1-4 ATS recently, unders 60%' },
};

const CONF_TRENDS = {
  MAC:  { favATS: 0.51, underPct: 0.54, titleFadeFav: true  },
  CUSA: { favATS: 0.48, underPct: 0.52, titleFadeFav: true  },
  A10:  { favATS: 0.54, underPct: 0.51, titleFadeFav: false },
  B12:  { favATS: 0.52, underPct: 0.56, titleFadeFav: false },
  SEC:  { favATS: 0.50, underPct: 0.54, titleFadeFav: false },
  ACC:  { favATS: 0.49, underPct: 0.52, titleFadeFav: true  },
  B10:  { favATS: 0.51, underPct: 0.55, titleFadeFav: false },
  BE:   { favATS: 0.50, underPct: 0.53, titleFadeFav: false },
  MVC:  { favATS: 0.53, underPct: 0.57, titleFadeFav: false },
  WCC:  { favATS: 0.52, underPct: 0.54, titleFadeFav: false },
};

const round = isNCAATourney ? detectRound(seedSum) : null;
const ncaaTrend = round ? NCAA_TRENDS[round] : null;
const confTrend = CONF_TRENDS[conf] || { favATS: 0.50, underPct: 0.52, titleFadeFav: false };
const trend = ncaaTrend || confTrend;
const trendNote = ncaaTrend
  ? `NCAA Tournament ${round} — ${ncaaTrend.note}`
  : `${conf} conf tournament — fav ATS ${(confTrend.favATS*100).toFixed(0)}%, under ${(confTrend.underPct*100).toFixed(0)}%${confTrend.titleFadeFav ? ', title game fav fade trend' : ''}`;
const isTitleGame = false; // wire in later when round detection is added

// Fetch MLB context if needed
let mlbContext = '';
if(sport === 'MLB') {
  const mlbData = await fetchMLBContext(game);
  if(mlbData) {
    const overUnder = mlbData.over_lean === true  ? 'OVER lean' :
                  mlbData.over_lean === false ? 'UNDER lean' :
                  'NEUTRAL — projected total not yet calculated for this game';
    const weatherNote = mlbData.wind_speed > 10 
      ? `Wind ${mlbData.wind_speed}mph ${mlbData.wind_direction} — ${mlbData.wind_direction === 'S' || mlbData.wind_direction === 'SW' ? 'blowing OUT (over lean)' : 'blowing IN (under lean)'}`
      : `${mlbData.temperature}°F, light wind`;
    mlbContext = `
MLB GAME CONTEXT:
- Venue: ${mlbData.venue}
- Park run factor: ${mlbData.park_run_factor} (${mlbData.park_run_factor > 103 ? 'hitter friendly' : mlbData.park_run_factor < 97 ? 'pitcher friendly' : 'neutral'})
- Weather: ${weatherNote}
- Temperature: ${mlbData.temperature}°F
- Precipitation: ${mlbData.precipitation > 0 ? mlbData.precipitation + 'mm — rain concern' : 'none'}
- Home starter: ${mlbData.home_pitcher || 'TBD'}${mlbData.home_days_rest ? ` (${mlbData.home_days_rest} days rest)` : ''}${mlbData.home_pitcher_home_era ? ` | Home ERA: ${mlbData.home_pitcher_home_era}` : ''}
- Away starter: ${mlbData.away_pitcher || 'TBD'}${mlbData.away_days_rest ? ` (${mlbData.away_days_rest} days rest)` : ''}${mlbData.away_pitcher_away_era ? ` | Away ERA: ${mlbData.away_pitcher_away_era}` : ''}
- Pitcher stats: ${mlbData.pitcher_context || 'not available'}
- Pitcher splits signal: ${mlbData.home_pitcher_home_era && mlbData.away_pitcher_away_era ? `${mlbData.home_pitcher} home ERA ${mlbData.home_pitcher_home_era} vs ${mlbData.away_pitcher} away ERA ${mlbData.away_pitcher_away_era}` : 'splits pending — early season'};
- Offensive quality: ${mlbData.home_woba ? `${game.home_team} wOBA ${mlbData.home_woba} / wRC+ ${mlbData.home_wrc_plus} ${mlbData.home_wrc_plus > 110 ? '⚡ elite offense' : mlbData.home_wrc_plus < 90 ? '⚠️ weak offense' : '(avg)'}` : 'wOBA pending'} | ${mlbData.away_woba ? `${game.away_team} wOBA ${mlbData.away_woba} / wRC+ ${mlbData.away_wrc_plus} ${mlbData.away_wrc_plus > 110 ? '⚡ elite offense' : mlbData.away_wrc_plus < 90 ? '⚠️ weak offense' : '(avg)'}` : 'wOBA pending'}
- Platoon-adjusted offense: ${mlbData.home_wrc_vs_opp_hand != null ? `${game.home_team} wRC+ vs opposing hand: ${mlbData.home_wrc_vs_opp_hand}${mlbData.home_wrc_plus ? ` (season ${mlbData.home_wrc_plus}, ${Math.abs(mlbData.home_wrc_vs_opp_hand - mlbData.home_wrc_plus) >= 15 ? '⚡ MATERIAL gap vs season' : 'in line'})` : ''}` : 'split pending'} | ${mlbData.away_wrc_vs_opp_hand != null ? `${game.away_team} wRC+ vs opposing hand: ${mlbData.away_wrc_vs_opp_hand}${mlbData.away_wrc_plus ? ` (season ${mlbData.away_wrc_plus}, ${Math.abs(mlbData.away_wrc_vs_opp_hand - mlbData.away_wrc_plus) >= 15 ? '⚡ MATERIAL gap vs season' : 'in line'})` : ''}` : 'split pending'}
- Pitcher recent form (last 3 starts): ${mlbData.home_pitcher_last_3_era != null ? `${mlbData.home_pitcher} ${mlbData.home_pitcher_last_3_era} ERA / ${mlbData.home_pitcher_last_3_k_pct || '?'}% K` : `${mlbData.home_pitcher || 'home SP'} L3 pending`} | ${mlbData.away_pitcher_last_3_era != null ? `${mlbData.away_pitcher} ${mlbData.away_pitcher_last_3_era} ERA / ${mlbData.away_pitcher_last_3_k_pct || '?'}% K` : `${mlbData.away_pitcher || 'away SP'} L3 pending`}
- K rate matchup: ${mlbData.home_k_gap !== null && mlbData.home_k_gap !== undefined ? `${mlbData.home_pitcher} K gap vs ${game.away_team} lineup: ${mlbData.home_k_gap > 0 ? '+' : ''}${mlbData.home_k_gap}pts ${Math.abs(mlbData.home_k_gap) >= 8 ? '⚡ LARGE K EDGE' : Math.abs(mlbData.home_k_gap) >= 4 ? '(notable)' : '(small)'}` : 'K gap: early season data pending'}
- ${mlbData.away_k_gap !== null && mlbData.away_k_gap !== undefined ? `${mlbData.away_pitcher} K gap vs ${game.home_team} lineup: ${mlbData.away_k_gap > 0 ? '+' : ''}${mlbData.away_k_gap}pts ${Math.abs(mlbData.away_k_gap) >= 8 ? '⚡ LARGE K EDGE' : Math.abs(mlbData.away_k_gap) >= 4 ? '(notable)' : '(small)'}` : ''}
- Days rest signal: ${mlbData.home_days_rest && mlbData.away_days_rest ? (mlbData.home_days_rest > mlbData.away_days_rest ? mlbData.home_pitcher + ' has rest advantage (' + mlbData.home_days_rest + ' vs ' + mlbData.away_days_rest + ' days)' : mlbData.away_days_rest > mlbData.home_days_rest ? mlbData.away_pitcher + ' has rest advantage (' + mlbData.away_days_rest + ' vs ' + mlbData.home_days_rest + ' days)' : 'Even rest') : 'TBD'}
- Umpire: ${mlbData.umpire_note || mlbData.umpire || 'TBD'}
- Model lean: ${overUnder}
- NRFI signal: ${mlbData.nrfi_score ? `Score ${mlbData.nrfi_score}/100 — ${mlbData.nrfi_score >= 95 ? 'VOLATILE tier — all signals maxed but historically a trap zone, flag the volatility' : mlbData.nrfi_score >= 90 ? 'PRIME NRFI tier — highest conviction zone, walk through both starters first-inning profiles' : mlbData.nrfi_score >= 80 ? 'NEUTRAL tier — do NOT frame as strong NRFI lean' : mlbData.nrfi_score >= 70 ? 'Mild NRFI lean' : mlbData.nrfi_score <= 35 ? 'Strong YRFI lean — runs expected early' : mlbData.nrfi_score <= 40 ? 'YRFI lean — offense expected early' : 'neutral first inning signal'}` : 'NRFI score pending'}
- Projected total: ${mlbData.projected_total ? mlbData.projected_total + ' (team stats + park + weather)' : 'NOT YET CALCULATED — do not infer a lean from pitcher data alone'}
- Total analysis: ${mlbData.projected_total ? 'Model total available' : 'IMPORTANT: No projected total available. Base total take only on posted line context and weather/park. Do not default to under.'}
- ${mlbData.home_runs_per_game ? `${game.home_team} offense: ${mlbData.home_runs_per_game.toFixed(2)} R/G, OPS ${mlbData.home_ops?.toFixed(3)}` : ''}
- ${mlbData.away_runs_per_game ? `${game.away_team} offense: ${mlbData.away_runs_per_game.toFixed(2)} R/G, OPS ${mlbData.away_ops?.toFixed(3)}` : ''}
- ${mlbData.home_bullpen_era ? `${game.home_team} bullpen: ${mlbData.home_bullpen_era} ERA, ${mlbData.home_save_pct}% save rate` : ''}
- ${mlbData.away_bullpen_era ? `${game.away_team} bullpen: ${mlbData.away_bullpen_era} ERA, ${mlbData.away_save_pct}% save rate` : ''}
- ${mlbData.home_record ? `${game.home_team} record: ${mlbData.home_record}, last 10: ${mlbData.home_last10 || 'N/A'}, streak: ${mlbData.home_streak || 'N/A'}` : ''}
- ${mlbData.away_record ? `${game.away_team} record: ${mlbData.away_record}, last 10: ${mlbData.away_last10 || 'N/A'}, streak: ${mlbData.away_streak || 'N/A'}` : ''}
- ${mlbData.lineup_confirmed ? `✅ Lineups confirmed` : '⏳ Lineups pending (available 2-3 hours before first pitch)'}
- ${mlbData.home_lineup ? `${game.home_team} lineup: ${mlbData.home_lineup}` : ''}
- ${mlbData.away_lineup ? `${game.away_team} lineup: ${mlbData.away_lineup}` : ''};
- Total delta: ${mlbData.projected_total && mlbData.projected_total > 0 ? (mlbData.projected_total - (game?.bookmakers?.[0]?.markets?.find(m=>m.key==='totals')?.outcomes?.[0]?.point || mlbData.projected_total)).toFixed(1) + ' pts vs posted line' : 'N/A'}
- Projected spread: ${mlbData.projected_spread != null ? `${mlbData.projected_spread > 0 ? game.home_team : game.away_team} by ${Math.abs(mlbData.projected_spread).toFixed(1)} runs` : 'N/A'}
- ML lean: ${mlbData.spread_delta != null ? (Math.abs(mlbData.spread_delta) >= 3.0 ? `${mlbData.spread_delta > 0 ? game.home_team : game.away_team} ML — spread delta ${mlbData.spread_delta > 0 ? '+' : ''}${mlbData.spread_delta.toFixed(1)} runs vs market (HIGH conviction tier)` : Math.abs(mlbData.spread_delta) >= 2.0 ? `Slight ${mlbData.spread_delta > 0 ? game.home_team : game.away_team} ML lean — delta ${mlbData.spread_delta > 0 ? '+' : ''}${mlbData.spread_delta.toFixed(1)}` : 'No strong ML lean — spread delta under 2.0') : 'N/A'}
- ML conviction: ${mlbData.spread_delta != null ? (Math.abs(mlbData.spread_delta) >= 3.0 ? 'HIGH — 3+ run spread delta' : Math.abs(mlbData.spread_delta) >= 2.0 ? 'MODERATE — 2+ run delta' : 'LOW — market and model agree') : 'N/A'}
- Spread delta: ${mlbData.spread_delta != null ? (mlbData.spread_delta > 0 ? '+' : '') + mlbData.spread_delta.toFixed(1) + ' runs vs posted line' : 'N/A'}
- First inning ERA: ${mlbData.home_first_inning_era != null ? mlbData.home_pitcher + ' 1st inn ERA ' + mlbData.home_first_inning_era : ''} ${mlbData.away_first_inning_era != null ? '| ' + mlbData.away_pitcher + ' 1st inn ERA ' + mlbData.away_first_inning_era : ''}
- Lineup strength: ${mlbData.home_lineup_weight != null ? `${game.home_team} ${mlbData.home_lineup_weight.toFixed(2)} weight${mlbData.home_lineup_ops ? ` (avg OPS ${mlbData.home_lineup_ops.toFixed(3)})` : ''}` : 'pending'} | ${mlbData.away_lineup_weight != null ? `${game.away_team} ${mlbData.away_lineup_weight.toFixed(2)} weight${mlbData.away_lineup_ops ? ` (avg OPS ${mlbData.away_lineup_ops.toFixed(3)})` : ''}` : 'pending'}${mlbData.home_lineup_weight != null && mlbData.away_lineup_weight != null && Math.abs(mlbData.home_lineup_weight - mlbData.away_lineup_weight) >= 2 ? ` ⚡ LARGE lineup gap (${Math.abs(mlbData.home_lineup_weight - mlbData.away_lineup_weight).toFixed(1)} pts)` : ''}
- Platoon numerical: ${mlbData.home_platoon_advantage != null ? `${game.home_team} ${mlbData.home_platoon_advantage > 0 ? '+' : ''}${mlbData.home_platoon_advantage.toFixed(1)}` : 'pending'} | ${mlbData.away_platoon_advantage != null ? `${game.away_team} ${mlbData.away_platoon_advantage > 0 ? '+' : ''}${mlbData.away_platoon_advantage.toFixed(1)}` : 'pending'} (positive = lineup platoon advantage vs opposing SP, negative = pitcher advantage, ±5 or more is material)
- Bullpen fatigue: ${mlbData.home_bp_relievers_3d != null ? `${game.home_team} ${mlbData.home_bp_relievers_3d} relievers used last 3d${mlbData.home_bp_relievers_3d >= 12 ? ' ⚠️ HIGH USAGE' : ''}` : ''} | ${mlbData.away_bp_relievers_3d != null ? `${game.away_team} ${mlbData.away_bp_relievers_3d} relievers used last 3d${mlbData.away_bp_relievers_3d >= 12 ? ' ⚠️ HIGH USAGE' : ''}` : ''}
- Travel context: ${mlbData.timezone_change != null && Math.abs(mlbData.timezone_change) >= 2 ? `${Math.abs(mlbData.timezone_change)}hr TZ change for away side — fatigue concern` : 'no material TZ change'}${mlbData.away_consecutive_road_games != null && mlbData.away_consecutive_road_games >= 6 ? ` • ${game.away_team} on ${mlbData.away_consecutive_road_games}-game road trip (road fatigue)` : mlbData.away_consecutive_road_games >= 4 ? ` • ${game.away_team} ${mlbData.away_consecutive_road_games}-game road trip` : ''}
- Defense (OAA): ${mlbData.home_team_oaa != null ? `${game.home_team} ${mlbData.home_team_oaa > 0 ? '+' : ''}${mlbData.home_team_oaa}` : ''} | ${mlbData.away_team_oaa != null ? `${game.away_team} ${mlbData.away_team_oaa > 0 ? '+' : ''}${mlbData.away_team_oaa}` : ''}${mlbData.home_team_oaa != null && mlbData.away_team_oaa != null && Math.abs(mlbData.home_team_oaa - mlbData.away_team_oaa) >= 10 ? ` ⚡ DEFENSE GAP ${Math.abs(mlbData.home_team_oaa - mlbData.away_team_oaa)} runs` : ''}
- Expected offense quality: ${mlbData.home_team_xwoba != null ? `${game.home_team} xwOBA ${mlbData.home_team_xwoba}${mlbData.home_woba ? ` vs actual ${mlbData.home_woba} (${(mlbData.home_team_xwoba - mlbData.home_woba).toFixed(3)} diff — ${Math.abs(mlbData.home_team_xwoba - mlbData.home_woba) >= 0.015 ? 'regression signal' : 'in line'})` : ''}` : ''} | ${mlbData.away_team_xwoba != null ? `${game.away_team} xwOBA ${mlbData.away_team_xwoba}${mlbData.away_woba ? ` vs actual ${mlbData.away_woba} (${(mlbData.away_team_xwoba - mlbData.away_woba).toFixed(3)} diff)` : ''}` : ''}
- Barrel/hard-hit: ${mlbData.home_team_barrel_pct != null ? `${game.home_team} ${mlbData.home_team_barrel_pct}% barrel` : ''} | ${mlbData.away_team_barrel_pct != null ? `${game.away_team} ${mlbData.away_team_barrel_pct}% barrel` : ''}
- Catcher framing: ${mlbData.home_catcher_framing != null ? `${game.home_team} catcher ${mlbData.home_catcher_framing > 0 ? '+' : ''}${mlbData.home_catcher_framing} framing runs` : ''} | ${mlbData.away_catcher_framing != null ? `${game.away_team} catcher ${mlbData.away_catcher_framing > 0 ? '+' : ''}${mlbData.away_catcher_framing} framing runs` : ''}${mlbData.home_catcher_framing != null && mlbData.away_catcher_framing != null && Math.abs(mlbData.home_catcher_framing - mlbData.away_catcher_framing) >= 5 ? ` ⚡ MATERIAL framing edge (K-prop implication)` : ''}
- Data confidence: ${mlbData.confidence}`;
  }
}
// Build NBA context for Jerry
let nbaContextStr = '';
if(sport === 'NBA') {
  const homeNBAData = nbaTeamData[game.home_team] ||
    Object.values(nbaTeamData).find(t => t.team && game.home_team.includes(t.team.split(' ').pop()));
  const awayNBAData = nbaTeamData[game.away_team] ||
    Object.values(nbaTeamData).find(t => t.team && game.away_team.includes(t.team.split(' ').pop()));
  if(homeNBAData && awayNBAData) {
    const netGap = (homeNBAData.net_rating - awayNBAData.net_rating).toFixed(1);
    const homeIsHome = true;
const homeNetAdj = homeNBAData.home_wins && homeNBAData.home_losses
  ? (homeNBAData.home_wins / (homeNBAData.home_wins + homeNBAData.home_losses) - 0.5) * 10
  : 0;
const awayNetAdj = awayNBAData.away_wins && awayNBAData.away_losses
  ? (awayNBAData.away_wins / (awayNBAData.away_wins + awayNBAData.away_losses) - 0.5) * 10
  : 0;

nbaContextStr = `
NBA EFFICIENCY DATA:
- ${game.home_team} net rating: ${homeNBAData.net_rating > 0 ? '+' : ''}${homeNBAData.net_rating.toFixed(1)} | eFG%: ${homeNBAData.efg_pct?.toFixed(1)}% | Pace: ${homeNBAData.pace?.toFixed(1)} | Record: ${homeNBAData.wins}-${homeNBAData.losses}
- ${game.home_team} home record: ${homeNBAData.home_record || 'N/A'} | Home net rating adj: ${homeNetAdj > 0 ? '+' : ''}${homeNetAdj.toFixed(1)}
- ${game.away_team} net rating: ${awayNBAData.net_rating > 0 ? '+' : ''}${awayNBAData.net_rating.toFixed(1)} | eFG%: ${awayNBAData.efg_pct?.toFixed(1)}% | Pace: ${awayNBAData.pace?.toFixed(1)} | Record: ${awayNBAData.wins}-${awayNBAData.losses}
- ${game.away_team} road record: ${awayNBAData.away_record || 'N/A'} | Road net rating adj: ${awayNetAdj > 0 ? '+' : ''}${awayNetAdj.toFixed(1)}
- Net rating gap: ${Math.abs(parseFloat(netGap)).toFixed(1)} pts favor ${parseFloat(netGap) > 0 ? game.home_team : game.away_team}
- Home/away context: ${game.home_team.split(' ').pop()} is ${homeNBAData.home_record || 'N/A'} at home | ${game.away_team.split(' ').pop()} is ${awayNBAData.away_record || 'N/A'} on road
- Last 5 net rating: ${game.home_team.split(' ').pop()} ${homeNBAData.last_10_net_rating > 0 ? '+' : ''}${homeNBAData.last_10_net_rating?.toFixed(1)} | ${game.away_team.split(' ').pop()} ${awayNBAData.last_10_net_rating > 0 ? '+' : ''}${awayNBAData.last_10_net_rating?.toFixed(1)}
- Defensive rating: ${game.home_team.split(' ').pop()} ${homeNBAData.defensive_rating?.toFixed(1)} (opp eFG%: ${homeNBAData.opp_efg_pct?.toFixed(1) || 'N/A'}%) | ${game.away_team.split(' ').pop()} ${awayNBAData.defensive_rating?.toFixed(1)} (opp eFG%: ${awayNBAData.opp_efg_pct?.toFixed(1) || 'N/A'}%)
- Avg pace: ${((homeNBAData.pace + awayNBAData.pace)/2).toFixed(1)} possessions/game
- SWEAT LOCKER NBA TOTAL MODEL: Projected ${scoreData?.projectedTotal || 'N/A'} pts (posted: ${scoreData?.postedTotal?.toFixed(1) || 'N/A'})${scoreData?.projectedTotal && scoreData?.postedTotal ? ` — delta: ${(parseFloat(scoreData.projectedTotal) - scoreData.postedTotal).toFixed(1)} pts ${Math.abs(parseFloat(scoreData.projectedTotal) - scoreData.postedTotal) >= 3 ? (parseFloat(scoreData.projectedTotal) > scoreData.postedTotal ? '→ OVER lean' : '→ UNDER lean') : '→ neutral'}` : ''}
- Model inputs: OffRtg cross-matched vs DefRtg, eFG% vs opp eFG%, pace-adjusted possessions, recent form drift, injury adjustments
${(nbaInjuryData[game.home_team] || []).length > 0 ? `- ${game.home_team} injuries: ${(nbaInjuryData[game.home_team] || []).filter(i => i.status === 'Out').map(i => i.player_name + ' (OUT)').concat((nbaInjuryData[game.home_team] || []).filter(i => i.status === 'Questionable').map(i => i.player_name + ' (Q)')).slice(0, 5).join(', ')}` : `- ${game.home_team}: no reported injuries`}
${(nbaInjuryData[game.away_team] || []).length > 0 ? `- ${game.away_team} injuries: ${(nbaInjuryData[game.away_team] || []).filter(i => i.status === 'Out').map(i => i.player_name + ' (OUT)').concat((nbaInjuryData[game.away_team] || []).filter(i => i.status === 'Questionable').map(i => i.player_name + ' (Q)')).slice(0, 5).join(', ')}` : `- ${game.away_team}: no reported injuries`}
${isPlayoffMode && (playoffSeries[game.home_team] || playoffSeries[game.away_team]) ?
  `- PLAYOFF SERIES: ${(playoffSeries[game.home_team] || playoffSeries[game.away_team]).series_label}
- Game number: ${(playoffSeries[game.home_team] || playoffSeries[game.away_team]).game_number}
- Is elimination game: ${(playoffSeries[game.home_team] || playoffSeries[game.away_team]).is_elimination ? 'YES — MUST WIN' : 'No'}`
  : ''}`;
  }
}
// Build UFC context for Jerry
let ufcContextStr = '';
if(sport === 'UFC') {
  try {
    // Fetch both fighters' stats from Supabase
    const fighterA = game.away_team; // UFC: away_team = fighter 1
    const fighterB = game.home_team; // UFC: home_team = fighter 2
    const { data: fighterAStats } = await supabase
      .from('ufc_fighter_stats')
      .select('*')
      .ilike('fighter_name', `%${fighterA.split(' ').pop()}%`)
      .limit(1)
      .single();
    const { data: fighterBStats } = await supabase
      .from('ufc_fighter_stats')
      .select('*')
      .ilike('fighter_name', `%${fighterB.split(' ').pop()}%`)
      .limit(1)
      .single();

    const formatFighter = (name, s) => {
      if(!s) return `${name}: stats not available`;
      return `${s.fighter_name} (${s.record || 'N/A'}) — SLpM ${s.slpm || 'N/A'}, str_acc ${s.str_acc || 'N/A'}%, SApM ${s.sapm || 'N/A'}, str_def ${s.str_def || 'N/A'}%, TD avg ${s.td_avg || 'N/A'}, TD acc ${s.td_acc || 'N/A'}%, TD def ${s.td_def || 'N/A'}%, sub avg ${s.sub_avg || 'N/A'}, finishing rate ${s.finishing_rate || 'N/A'}%. Stance: ${s.stance || 'N/A'}. Wins: ${s.wins_by_ko || 0} KO, ${s.wins_by_sub || 0} SUB, ${s.wins_by_dec || 0} DEC.`;
    };

    ufcContextStr = `
UFC FIGHT CONTEXT:
- Fighter A: ${formatFighter(fighterA, fighterAStats)}
- Fighter B: ${formatFighter(fighterB, fighterBStats)}
- Moneyline: ${fighterA} ${game.bookmakers?.[0]?.markets?.find(m=>m.key==='h2h')?.outcomes?.[0]?.price || 'N/A'} / ${fighterB} ${game.bookmakers?.[0]?.markets?.find(m=>m.key==='h2h')?.outcomes?.[1]?.price || 'N/A'}
${fighterAStats && fighterBStats ? `- Striking gap: SLpM diff ${((fighterAStats.slpm||0) - (fighterBStats.slpm||0)).toFixed(1)}, accuracy gap ${((fighterAStats.str_acc||0) - (fighterBStats.str_acc||0)).toFixed(1)}%
- Grappling: ${fighterAStats.fighter_name} TD avg ${fighterAStats.td_avg||0}/15min vs ${fighterBStats.fighter_name} TD def ${fighterBStats.td_def||0}%
- Finishing: ${fighterAStats.fighter_name} ${fighterAStats.finishing_rate||0}% vs ${fighterBStats.fighter_name} ${fighterBStats.finishing_rate||0}%` : ''}`;
  } catch(e) {
    //console.log('UFC context error:', e);
  }
}
const modelContext = scoreData?.predictedSpread ? `
SWEAT LOCKER MODEL DATA:
- Projected spread: ${predictedSpread > 0 ? homeName : awayName} by ${Math.abs(predictedSpread).toFixed(1)}
- Edge vs posted line: ${spreadEdge > 0 ? '+' : ''}${spreadEdge.toFixed(1)} pts ${Math.abs(spreadEdge) >= 3 ? '⚠️ SIGNIFICANT' : Math.abs(spreadEdge) >= 1.5 ? '(notable)' : '(small)'}
- Win probability: ${homeWP ? `${homeName} ${(homeWP*100).toFixed(0)}% / ${awayName} ${((1-homeWP)*100).toFixed(0)}%` : 'N/A'}
- Model best bet: ${modelBestBet || 'No strong lean'}
- Model lean side: ${scoreData?.leanSide || 'N/A'}
- Net efficiency edge: ${ncaabBreakdown?.mismatchPts > 0 ? homeName : awayName} +${Math.abs(ncaabBreakdown?.mismatchPts || 0).toFixed(1)} pts
- Top mismatches: ${ncaabBreakdown?.efgMismatch || 'N/A'}
- Posted total: ${postedTotal || 'N/A'}
- Projected total: ${projTotal || 'N/A'} → ${totalLeanLabel}
- Total delta: ${totalDelta !== null ? (totalDelta > 0 ? '+' : '') + totalDelta + ' pts vs posted' : 'N/A'}
- Defensive pace context: ${awayTeamData && homeTeamData ? `avg AdjDE ${((awayTeamData.adjDE + homeTeamData.adjDE)/2).toFixed(1)}, avg tempo ${((awayTeamData.tempo + homeTeamData.tempo)/2).toFixed(1)} pos/40` : 'N/A'}
- Pace: ${awayTeamData && homeTeamData ? `${awayName} ${awayTeamData.tempo?.toFixed(1)} pos/40 (rank #${awayTeamData.tempoRank}) vs ${homeName} ${homeTeamData.tempo?.toFixed(1)} pos/40 (rank #${homeTeamData.tempoRank})` : 'N/A'}
- SOS gap: ${Math.abs(sosDelta).toFixed(2)} ${Math.abs(sosDelta) > 3 ? '(LARGE — significant schedule strength difference)' : Math.abs(sosDelta) > 1.5 ? '(moderate)' : '(small)'}
- Luck factor: ${luckAdjustment > 0.5 ? `${awayName} unlucky — true talent better than record` : luckAdjustment < -0.5 ? `${homeName} unlucky — true talent better than record` : 'Neutral'}
${awayTeamData ? `- ${awayName} efficiency: AdjOE ${awayTeamData.adjOE?.toFixed(1)} (#${awayTeamData.adjOERank}) / AdjDE ${awayTeamData.adjDE?.toFixed(1)} (#${awayTeamData.adjDERank}) / Record: ${awayTeamData.wins}-${awayTeamData.losses}` : ''}
${homeTeamData ? `- ${homeName} efficiency: AdjOE ${homeTeamData.adjOE?.toFixed(1)} (#${homeTeamData.adjOERank}) / AdjDE ${homeTeamData.adjDE?.toFixed(1)} (#${homeTeamData.adjDERank}) / Record: ${homeTeamData.wins}-${homeTeamData.losses}` : ''}
${awayTeamData ? `- ${awayName} four factors: eFG% off ${awayTeamData.eFG_O?.toFixed(1)}% (#${awayTeamData.eFG_O_rank}) / eFG% def ${awayTeamData.eFG_D?.toFixed(1)}% (#${awayTeamData.eFG_D_rank}) / OR% #${awayTeamData.or_O_rank} / TO% forced #${awayTeamData.to_D_rank}` : ''}
${homeTeamData ? `- ${homeName} four factors: eFG% off ${homeTeamData.eFG_O?.toFixed(1)}% (#${homeTeamData.eFG_O_rank}) / eFG% def ${homeTeamData.eFG_D?.toFixed(1)}% (#${homeTeamData.eFG_D_rank}) / OR% #${homeTeamData.or_O_rank} / TO% forced #${homeTeamData.to_D_rank}` : ''}
- Tournament context: ${trendNote}
- Seeds: ${isNCAATourney ? `${awayName} #${awaySeed} vs ${homeName} #${homeSeed} — ${round}` : 'Conference tournament'}
- Under lean strength: ${(trend.underPct*100).toFixed(0)}% historical under rate this round/conf
- Fav ATS rate: ${(trend.favATS*100).toFixed(0)}% this round/conf
${lineMoveTeam ? `- Sharp line movement: ${lineMovePoints} pts toward ${lineMoveTeam} — ${lineMovePoints >= 2 ? '⚠️ SIGNIFICANT sharp action' : 'notable movement'}` : '- Line movement: No significant movement detected'}
${scoreData?.efgMismatch && sport === 'NBA' ? `- Back-to-back: ${scoreData.efgMismatch}` : ''}
` : '';

const gameTime = new Date(game.commence_time);
const isLive = new Date() > gameTime && new Date() < new Date(gameTime.getTime() + 3*60*60*1000);
if(isLive) {
  setGameNarrative('⚡ Game in progress — Jerry\'s pre-game analysis is locked. Check live lines for current action.');
  setGameNarrativeLoading(false);
  return;
}

    setGameNarrative('');
    setGameNarrativeLoading(true);

    // Check Supabase cache first
    const gameKey = (game.id || (game.away_team + '_' + game.home_team)) + '_' + new Date().toISOString().split('T')[0];
    try {
      const { data: cachedNarrative } = await supabase
        .from('jerry_cache')
        .select('narrative, created_at')
        .eq('game_id', gameKey)
        .eq('sport', sport)
        .single();
      if(cachedNarrative) {
        const ageMin = (Date.now() - new Date(cachedNarrative.created_at).getTime()) / 60000;
        if(ageMin < 480) {
          setGameNarrative(cachedNarrative.narrative);
          setGameNarrativeLoading(false);
          return;
        }
      }
    } catch(e) {}

    try {
      const dataQualityNote = sport === 'NCAAB'
  ? `NOTE: Full KenPom efficiency model active — four factors, tempo, efficiency gaps all available.`
  : sport === 'MLB'
  ? `NOTE: Sweat Locker MLB model active — pitcher xERA, wOBA/wRC+, K rate gap, platoon advantage, bullpen ERA, park factor, weather, umpire tendencies all feeding the model. Use these signals specifically.`
  : sport === 'NBA'
  ? `NOTE: Sweat Locker NBA model active — net rating, defensive rating, opp eFG%, home/away records, last 5 net rating, injury report, pace matchup all feeding the model. Use these signals specifically.`
  : `NOTE: Market-based analysis only for ${sport}. Be transparent about this limitation.`;
      const spread = game?.bookmakers?.[0]?.markets?.find(m=>m.key==='spreads')?.outcomes?.[0];
const total = game?.bookmakers?.[0]?.markets?.find(m=>m.key==='totals')?.outcomes?.[0];
  totalDelta <= -4 ? `STRONG UNDER (model ${Math.abs(totalDelta)} pts below posted — heavy under lean)` :
  totalDelta <= -2 ? `UNDER lean (model ${Math.abs(totalDelta)} pts below posted)` :
  totalDelta >= 4  ? `STRONG OVER (model ${Math.abs(totalDelta)} pts above posted — heavy over lean)` :
  totalDelta >= 2  ? `OVER lean (model ${totalDelta} pts above posted)` :
  `NEUTRAL (model within ${Math.abs(totalDelta)} pts of posted — no strong lean)`;
  // Build Sweat Score signal context to pass to Jerry
const sweatSignals = [];
if(scoreData) {
  if(sport === 'MLB' && mlbContext) {
    const mlbData = await fetchMLBContext(game);
    if(mlbData) {
      if(mlbData.projected_total && mlbData.projected_total > 0) {
        const total = game?.bookmakers?.[0]?.markets?.find(m=>m.key==='totals')?.outcomes?.[0]?.point;
        const delta = total ? (mlbData.projected_total - total).toFixed(1) : null;
        if(delta) sweatSignals.push(`Model projects ${mlbData.projected_total} runs vs market ${total} → ${parseFloat(delta) < 0 ? 'UNDER' : 'OVER'} lean (${delta} run gap)`);
      }
      if(mlbData.home_k_gap && Math.abs(mlbData.home_k_gap) >= 4) sweatSignals.push(`K rate gap: ${mlbData.home_pitcher} ${mlbData.home_k_gap > 0 ? '+' : ''}${mlbData.home_k_gap}pts vs ${game.away_team} lineup`);
      if(mlbData.away_k_gap && Math.abs(mlbData.away_k_gap) >= 4) sweatSignals.push(`K rate gap: ${mlbData.away_pitcher} ${mlbData.away_k_gap > 0 ? '+' : ''}${mlbData.away_k_gap}pts vs ${game.home_team} lineup`);
      if(mlbData.home_wrc_plus && mlbData.away_wrc_plus) {
        const wrcGap = mlbData.home_wrc_plus - mlbData.away_wrc_plus;
        if(Math.abs(wrcGap) >= 10) sweatSignals.push(`wRC+ edge: ${wrcGap > 0 ? game.home_team : game.away_team} +${Math.abs(wrcGap)} wRC+ advantage`);
      }
      if(mlbData.home_platoon_note) sweatSignals.push(`Platoon: ${mlbData.home_platoon_note}`);
      if(mlbData.away_platoon_note) sweatSignals.push(`Platoon: ${mlbData.away_platoon_note}`);
      if(mlbData.spread_delta != null && Math.abs(mlbData.spread_delta) >= 2.0) {
        const mlFav = mlbData.spread_delta > 0 ? game.home_team : game.away_team;
        const mlDelta = Math.abs(mlbData.spread_delta).toFixed(1);
        sweatSignals.push(`ML lean: ${mlFav} (${mlDelta} run spread delta vs market${Math.abs(mlbData.spread_delta) >= 3.0 ? ' — HIGH conviction' : ''})`);
      }
    }
  }
  if(sport === 'NBA') {
    const homeNBA = Object.values(nbaTeamData).find(t => t.team && game.home_team.includes(t.team.split(' ').pop()));
    const awayNBA = Object.values(nbaTeamData).find(t => t.team && game.away_team.includes(t.team.split(' ').pop()));
    if(homeNBA && awayNBA) {
      const netGap = homeNBA.net_rating - awayNBA.net_rating;
      if(Math.abs(netGap) >= 3) sweatSignals.push(`Net rating gap: ${netGap > 0 ? game.home_team : game.away_team} +${Math.abs(netGap).toFixed(1)} pts advantage`);
      if(homeNBA.defensive_rating && awayNBA.defensive_rating) {
        const defGap = awayNBA.defensive_rating - homeNBA.defensive_rating;
        if(Math.abs(defGap) >= 3) sweatSignals.push(`Defensive edge: ${defGap > 0 ? game.home_team : game.away_team} ${Math.abs(defGap).toFixed(1)} pts better DefRtg`);
      }
      const homeWinPct = homeNBA.home_wins/(homeNBA.home_wins+homeNBA.home_losses||1);
      const awayWinPct = awayNBA.away_wins/(awayNBA.away_wins+awayNBA.away_losses||1);
      if(homeWinPct - awayWinPct >= 0.15) sweatSignals.push(`Situational edge: ${game.home_team} ${homeNBA.home_record} at home vs ${game.away_team} ${awayNBA.away_record} on road`);
      if(homeNBA.injury_note?.includes('OUT')) sweatSignals.push(`⚠️ ${game.home_team} injuries: ${homeNBA.injury_note}`);
      if(awayNBA.injury_note?.includes('OUT')) sweatSignals.push(`⚠️ ${game.away_team} injuries: ${awayNBA.injury_note}`);
    }
  }
  if(scoreData.leanSide) sweatSignals.push(`Model lean: ${scoreData.leanSide}`);
}
const sweatScoreContext = sweatSignals.length > 0 
  ? `\nSWEAT LOCKER MODEL SIGNALS (Score: ${scoreData.total}/100):\n${sweatSignals.map(s => `- ${s}`).join('\n')}`
  : '';    
  const todayET = new Date().toLocaleDateString('en-US', {timeZone: 'America/New_York', year: 'numeric', month: 'long', day: 'numeric', weekday: 'long'});

  const sportRules = {
    MLB: `
FORMAT: Write a structured game prep using these markdown section headers. Skip any section that has no material data. Each section is 1-3 sentences — short, specific, no padding.

**The Setup**
One-line matchup frame. Reference Sweat Score tier (PRIME SWEAT / Strong Lean / Best Available) as context, not as the pitch.

**The Pitcher Matchup**
Lead with the biggest pitching edge: xERA gap, K rate gap, or form drift. Always reference handedness (RHP/LHP). If pitcher's last-3 ERA differs from season xERA by 1.5+, call out the form drift. Use specific numbers cited from the data.

**Lineup Quality**
wRC+ for both teams. If platoon-adjusted wRC+ (vs opposing pitcher's hand) differs from season wRC+ by 15+ pts, lead with that gap — it matters more than the raw season number. Flag elite (>110) or weak (<90) offenses. Reference platoon note if lineups confirmed.

**Total Lean**
Projected total vs posted line with the delta in runs. If ≥4 runs = STRONG lean, cite run environment (R/G, park, weather, bullpens). If ≥2 = lean. If <2 = no edge on total. Include park/weather ONLY if material (wind 15+mph, Coors, heavy rain, extreme temp).

**Where the Model Sits**
Summarize the signal state: ML spread delta with conviction tier (HIGH ≥3 runs / MODERATE 2-3 / LOW <2), NRFI tier, any K-friendly ump or notable sharp movement. Name what's driving the conviction.

**The Play**
One directional sentence. Natural close — DO NOT repeat phrases across games. Vary sign-off: "data points to...", "signals align on...", "edge lives on...", "model's angle here is...", or just close with the specific matchup insight. Never "lock it in", "smash this", "take this", "must play", "bet".

CONVICTION THRESHOLDS:
- ML delta ≥3 runs = HIGH conviction — feature in Pitcher Matchup AND Play
- ML delta 2-3 = MODERATE — frame as "model slightly favors X"
- ML delta <2 = SKIP ML entirely — stick to total + NRFI
- NRFI 95+ = VOLATILE tier (historically a trap zone) — flag the volatility
- NRFI 90-94 = PRIME tier (highest conviction zone) — walk through both starters' first-inning profiles
- NRFI 80-89 = NEUTRAL — do NOT frame as NRFI lean
- NRFI 70-79 = mild NRFI lean
- NRFI ≤35 = strong YRFI lean

NRFI vs TOTAL CONFLICT:
- High NRFI + high projected total is NOT a contradiction. Elite starters suppress inning 1 while bullpens allow runs later. Resolve in one sentence.

NO PROJECTED TOTAL:
- If projected_total = "NOT YET CALCULATED", give a neutral total take. Do NOT default to under.

TONE:
- Sharp analyst writing pre-game prep, not tweet. Confident, specific, numbers-cited.
- Analyst, not tout. "Here's what stands out" / "The model sees an edge" / "The data points to".
- Reference Sweat Score naturally ("grades at 72") — signals do the talking, not the score.
- K-friendly ump favors unders + strikeout props.

OVERRIDE:
- Only override model lean for concrete breaking news (scratch, injury, weather flip, lineup change) from web search. Include in Setup or Play section. Say "Override: [reason]".
- NEVER override based on gut feel or market consensus.

SIGNAL COVERAGE:
- Reference every material signal available in the data (streak, days rest, L3 form drift, platoon gap, park, weather, ump tendencies, bullpen fatigue, confirmed lineups, pitcher vs team history, team defense OAA, catcher framing, expected vs actual wOBA) — AND briefly explain why each matters for THIS specific matchup.
- Team defense (OAA): gap of 10+ runs favors better defense on totals (unders) and close games. Mention only when gap is material.
- Catcher framing: 5+ run framing gap is a K-prop and NRFI signal — elite framer expands the strike zone for his pitcher.
- Expected vs actual wOBA: if team xwOBA differs from actual wOBA by 0.020+, flag regression (hot teams over-performing come back, cold teams under-performing bounce).
- Don't just list signals. Tie each to outcome implication like a sharp analyst. Example voice: "Mets on a 12-game skid, due for bounce-back — but today's lineup facing [pitcher type] in [park context] makes it hard to see that happening here."
- Silent signals (available in data but unmentioned) are wasted context. If it's in the data and material, it gets one line of interpretation.

LENGTH: Usually 6-12 sentences total across sections. Skip empty sections. No padding — if data isn't material, don't invent filler.`,

    NBA: `
LEAD SIGNAL HIERARCHY (lead with the first one that fires):
1. Star OUT = lead immediately. OUT affects spread 4-10 pts depending on player — always quantify the impact. Overrides everything else.
2. Key player Questionable = flag and note the line hasn't fully priced it in.
3. Back-to-back = always mention. Fade the B2B team unless line already moved 3+ pts against them.
4. Home/away record mismatch (34-7 home vs 14-27 road = massive situational edge — lean home regardless of net rating gap).
5. Net rating gap ≥3 = real edge. Reference specifically.
6. Defensive rating gap ≥3, opp eFG% edge.
7. Pace differential + last 5 net rating (form drift).
8. Total delta ≥3 = lean over/under. Cite pace + defenses + eFG% + form + injuries.

PLAYOFFS (only if isPlayoffMode true):
- Lead with series context — who leads, what game, elimination scenario.
- Home court is earned — series leader at home is a massive edge.
- Elimination games play differently — flag immediately.
- Down 3-1 historical comeback rate is 7%.

TONE: Confident, direct. Reference specific numbers. No generic "sharp money" filler.

OVERRIDE:
- Web search for tonight's injury report FIRST — real-world factors override market lean.
- If game already played, say so and stop.

LENGTH: 2-3 sentences. Hard cap.`,

    NCAAB: `
LEAD SIGNALS:
- Base analysis ONLY on model data provided — no outside knowledge.
- Efficiency gap (Sweat Locker four-factors + tempo).
- If FanMatch active: lead with model game prediction.
- If FanMatch not active: lead with season efficiency, note you're working from season-long data.

RULES:
- Never name KenPom — call it the "Sweat Locker model".
- Tournament games are neutral site — do NOT mention home court.
- No web search — model-only analysis.

LENGTH: 2-3 sentences. Hard cap.`,

    UFC: `
LEAD SIGNAL HIERARCHY:
1. Finishing rate — single most important stat. 80%+ finisher vs decision fighter = massive style edge, lead with it.
2. SLpM gap — who controls striking distance.
3. TD defense vs TD average — grappling matchup. 70% TD def vs 4 TD/fight average neutralizes grappling.
4. Stylistic matchup (striker vs grappler, reach, cardio).

STRUCTURE (3 sentences — hard cap):
- Sentence 1: What the MODEL says (SLpM gap, finish rate, TD defense — specific numbers from UFC FIGHT CONTEXT).
- Sentence 2: What PUBLIC ANALYSTS say (web search Doc Sports, Covers MMA, MMA Fighting, MMA Decisions, BestFightOdds — name source).
- Sentence 3: Where model and analysts AGREE or DISAGREE. If they diverge, explain why. THAT is the edge.

LENGTH: 3 sentences. Hard cap.`,

    NHL: `
TRANSPARENCY:
- Open with one line: "Market-based analysis — no NHL model active yet."
- Do NOT fabricate model metrics.

LEAD SIGNALS:
- Confirmed goalie starters (most important signal — web search for today's starters).
- Pace, special teams, recent form.
- Line movement ≥2pts = flag.

LENGTH: 2-3 sentences. Hard cap.`,
  };

  const universalRules = `
UNIVERSAL RULES:
- This is a PRE-GAME take. NEVER recap a completed game. If game is already live or played, say "This game has already started — Jerry's pre-game read is locked." and stop.
- NEVER refuse to give a directional lean. NEVER ask the user to clarify.
- If sharp line movement ≥2pts, mention it.
- If total delta ≥4pts, mention the over/under lean specifically.
- Reference the Sweat Score naturally, don't hard-sell it.
- Never say "bet" or "must play".`;

  const prompt = `CRITICAL: TODAY'S DATE IS ${todayET}. Ignore your internal date knowledge — use ONLY the date stated here. This is a PRE-GAME analysis for a game that has NOT yet been played. Do NOT search for scores or results. Assume the game starts soon. Analyze the matchup data directly.

You are Jerry, a sharp sports analyst for The Sweat Locker. Confident, direct, no fluff.

OUTPUT RULES (read before anything else):
- NEVER preamble. Do NOT write "Let me look at...", "Let me search...", "Let me analyze...", "Based on the data...", "Looking at this matchup...", "Alright, let's break this down...", or any lead-in phrase. Jump straight to the analysis.
- NEVER narrate your process. Start immediately with the first section header (if structured format) or the first signal (if compact format).
- NEVER repeat a closing phrase across reads. Do NOT end every take with "That's where the model sits", "Those are the signals", or any single template line — vary naturally per game.
- Never "lock it in" / "this is the play" / "must play" / "bet" / "smash this".
- If the game has already started or been played, say "This game has already started — Jerry's pre-game read is locked." and stop.

GAME: ${game.away_team} @ ${game.home_team}
SCHEDULED: ${new Date(game.commence_time).toLocaleString('en-US', {timeZone: 'America/New_York'})} ET
SPORT: ${sport}
SWEAT SCORE: ${scoreData.total}/100 ${scoreData.total >= 68 ? '🔒 PRIME SWEAT' : scoreData.total >= 62 ? '— Strong Lean' : '— Best Available Today'}
SPREAD: ${spread ? `${spread.name} ${spread.point > 0 ? '+' : ''}${spread.point}` : 'N/A'}
TOTAL: ${total ? total.point : 'N/A'}
MODEL LEAN: ${scoreData?.leanSide || 'N/A'}
CONFIDENCE TIER: ${
  sport === 'NCAAB' && scoreData.hasFanmatch ? 'HIGH — Sweat Locker game model active (fanmatch + four factors)' :
  sport === 'NCAAB' ? 'MODERATE — efficiency model only, no game prediction' :
  sport === 'NBA' ? 'HIGH — NBA model active (net rating, DefRtg, opp eFG%, home/away records, injuries, pace)' :
  sport === 'MLB' ? 'HIGH — MLB model active (pitcher xERA, wOBA, K rate gap, platoon, bullpen, park, weather, umpire)' :
  sport === 'UFC' ? 'MODERATE — fighter stats + public analyst consensus' :
  sport === 'NHL' ? 'MARKET — no NHL model, pure market analysis' :
  'MODERATE — limited model coverage'
}
${scoreData.isTournamentFloor ? 'Note: Best available play today — not a Prime Sweat. Measured tone.' : ''}
${sweatScoreContext}
${modelContext}
${sport === 'MLB' ? mlbContext : ''}
${sport === 'NBA' ? nbaContextStr : ''}
${sport === 'UFC' ? ufcContextStr : ''}

=== ${sport} RULES ===
${sportRules[sport] || sportRules.NHL}

${universalRules}

${dataQualityNote}`;

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      const response = await fetch('https://api.anthropic.com/v1/messages',{
        method:'POST',
        signal: controller.signal,
        headers:{
          'Content-Type':'application/json',
          'x-api-key':ANTHROPIC_API_KEY,
          'anthropic-version':'2023-06-01',
          'anthropic-dangerous-direct-browser-access':'true'
        },
        body:JSON.stringify({
  model:'claude-haiku-4-5-20251001',
  max_tokens:1000,
  ...(sport === 'NBA' || sport === 'NFL' || sport === 'MLB' || sport === 'UFC' || sport === 'NHL' ? {tools:[{type:'web_search_20250305',name:'web_search'}]} : {}),
  messages:[{role:'user',content:prompt}]
})
      });
      clearTimeout(timeout);
      const data = await response.json();
      //console.log('Jerry response status:', response.status);
      //console.log('Jerry response data:', JSON.stringify(data));
      const text = data?.content?.filter(b=>b.type==='text').map(b=>b.text).join('') || '';
      setGameNarrative(text);
      // Save to Supabase cache
      if(text) {
        try {
          await supabase.from('jerry_cache').upsert({
            game_id: gameKey,
            sport: sport,
            narrative: text,
            created_at: new Date().toISOString(),
          }, {onConflict: 'game_id,sport'});
        } catch(e) {}
      };
    } catch(e) {
      //console.log('Jerry error:', e.message);
      setGameNarrative('Jerry is reviewing the tape on this one. Check back shortly.');
    }
    setGameNarrativeLoading(false);
  };
  
  const fetchAltLines = async (game, sport) => {
  if(!game) return;
  const key = game.id || (game.away_team + game.home_team);
  if(altLines[key]) return;
  if(altLinesLoading[key]) return;
  setAltLinesLoading(prev => ({...prev, [key]: true}));
  try {
    const sportKey = SPORT_KEYS[sport];
    if(!sportKey) return;
    const r = await axios.get(`https://api.the-odds-api.com/v4/sports/${sportKey}/events/${game.id}/odds`, {
      params: {
        apiKey: ODDS_API_KEY,
        regions: 'us,us2',
        markets: 'alternate_spreads,alternate_totals,h2h_1st_5_innings,totals_1st_5_innings,pitcher_props',
        oddsFormat: 'american',
        bookmakers: 'hardrockbet,draftkings,fanduel,betmgm'
      }
    });
    const bookmakers = r.data?.bookmakers || [];
    const altSpreads = [];
    const altTotals = [];
    bookmakers.forEach(bm => {
      bm.markets?.forEach(mkt => {
        if(mkt.key === 'alternate_spreads') {
          mkt.outcomes?.forEach(outcome => {
            altSpreads.push({
              book: BOOKMAKER_MAP[bm.key] || bm.key,
              name: outcome.name,
              point: outcome.point,
              odds: outcome.price,
              isHRB: (BOOKMAKER_MAP[bm.key] || bm.key) === HRB
            });
          });
        }
        if(mkt.key === 'alternate_totals') {
          mkt.outcomes?.forEach(outcome => {
            altTotals.push({
              book: BOOKMAKER_MAP[bm.key] || bm.key,
              name: outcome.name,
              point: outcome.point,
              odds: outcome.price,
              isHRB: (BOOKMAKER_MAP[bm.key] || bm.key) === HRB
            });
          });
        }
        if(mkt.key === 'h2h_1st_5_innings') {
  mkt.outcomes?.forEach(outcome => {
    altSpreads.push({
      book: BOOKMAKER_MAP[bm.key] || bm.key,
      name: outcome.name,
      point: null,
      odds: outcome.price,
      isHRB: (BOOKMAKER_MAP[bm.key] || bm.key) === HRB,
      isF5: true,
      label: 'F5 ML'
    });
  });
}
if(mkt.key === 'totals_1st_5_innings') {
  mkt.outcomes?.forEach(outcome => {
    altTotals.push({
      book: BOOKMAKER_MAP[bm.key] || bm.key,
      name: outcome.name,
      point: outcome.point,
      odds: outcome.price,
      isHRB: (BOOKMAKER_MAP[bm.key] || bm.key) === HRB,
      isF5: true,
      label: `F5 ${outcome.name} ${outcome.point}`
    });
  });
}
if(mkt.key === 'pitcher_props') {
  mkt.outcomes?.forEach(outcome => {
    if(outcome.description?.toLowerCase().includes('no run') || 
       outcome.name?.toLowerCase().includes('nrfi') ||
       outcome.description?.toLowerCase().includes('nrfi')) {
      altTotals.push({
        book: BOOKMAKER_MAP[bm.key] || bm.key,
        name: 'NRFI',
        point: null,
        odds: outcome.price,
        isHRB: (BOOKMAKER_MAP[bm.key] || bm.key) === HRB,
        isNRFI: true,
        label: 'NRFI'
      });
    }
  });
}
      });
    });
    // Sort by point value
    altSpreads.sort((a,b) => a.point - b.point);
    altTotals.sort((a,b) => a.point - b.point);
    setAltLines(prev => ({...prev, [key]: {altSpreads, altTotals}}));
  } catch(e) {}
  setAltLinesLoading(prev => ({...prev, [key]: false}));
};
  const fetchHistoricalOdds = async (game, sport) => {
    if(!game) return null;
    const key = game.id || (game.away_team+game.home_team);
    if(historicalOdds[key]) return historicalOdds[key];
    if(historicalOddsLoading[key]) return null;
    setHistoricalOddsLoading(prev => ({...prev, [key]: true}));
    try {
      const sportKey = SPORT_KEYS[sport];
      if(!sportKey) return null;
      const gameTime = new Date(game.commence_time);
      const fetchTime = new Date(gameTime.getTime() - 24*60*60*1000);
      const dateStr = fetchTime.toISOString().replace('.000Z', 'Z');
      const r = await axios.get(`https://api.the-odds-api.com/v4/historical/sports/${sportKey}/odds/`, {
        params: {
          apiKey: ODDS_API_KEY,
          date: dateStr,
          regions: 'us,us2',
          markets: 'spreads,totals,h2h',
          dateFormat: 'iso',
          oddsFormat: 'american',
        }
      });
      const games = r.data?.data || [];
      const match = games.find(g =>
        fuzzyMatch(g.away_team, game.away_team) > 0.7 &&
        fuzzyMatch(g.home_team, game.home_team) > 0.7
      );
      //console.log('Match found:', !!match);
      if(!match) {
        setHistoricalOddsLoading(prev => ({...prev, [key]: false}));
        return null;
      }
      const result = {
        openingSpread: null,
        openingTotal: null,
        openingML: null,
        timestamp: dateStr,
      };
      const hrbBm = match.bookmakers?.find(bm => bm.key===HRB) || match.bookmakers?.[0];
      if(hrbBm) {
        const spreadMkt = hrbBm.markets?.find(m => m.key==='spreads');
        const totalMkt = hrbBm.markets?.find(m => m.key==='totals');
        const mlMkt = hrbBm.markets?.find(m => m.key==='h2h');
        if(spreadMkt?.outcomes?.[0]) result.openingSpread = spreadMkt.outcomes[0].point;
        if(totalMkt?.outcomes?.[0]) result.openingTotal = totalMkt.outcomes[0].point;
        if(mlMkt?.outcomes?.[0]) result.openingML = mlMkt.outcomes[0].price;
      }
      setHistoricalOdds(prev => ({...prev, [key]: result}));
      setHistoricalOddsLoading(prev => ({...prev, [key]: false}));
      return result;
    } catch(e) {
      //console.log('Historical odds error:', sport, SPORT_KEYS, e?.message, e?.response?.status);
      setHistoricalOddsLoading(prev => ({...prev, [key]: false}));
      return null;
    }
  };
  const fetchPropHistory = async (player, stat='pts') => {
    if(!player) return;
    setPropHistoryLoading(true);
    setPropHistoryData([]);
    try {
      const searchResp = await axios.get('https://api.balldontlie.io/v1/players', {
        headers: {'Authorization': BDL_API_KEY},
        params: {search: player.name.split(' ')[1]||player.name, per_page: 5}
      });
      //console.log('BDL search resp:', searchResp?.status);
      const players = searchResp.data?.data || [];
      //console.log('Players found:', players.length);
      if(players.length === 0) { setPropHistoryLoading(false); return; }
      const bdlPlayer = players[0];
      //console.log('BDL player:', bdlPlayer.id, bdlPlayer.first_name, bdlPlayer.last_name);
      
      const statsResp = await axios.get('https://api.balldontlie.io/v1/stats', {
        headers: {'Authorization': BDL_API_KEY},
        timeout: 10000,
        params: {'player_ids[]': bdlPlayer.id, per_page: 15}
      });
      //console.log('Stats status:', statsResp?.status);
      //console.log('Stats count:', statsResp?.data?.data?.length);
      const games = statsResp.data?.data || [];
      const sorted = games
        .filter(g => g.min && g.min !== '0' && g.min !== '00')
        .sort((a,b) => new Date(b.game.date) - new Date(a.game.date))
        .slice(0,10)
        .map(g => ({
          date: new Date(g.game.date).toLocaleDateString('en-US',{month:'numeric',day:'numeric'}),
          pts: g.pts || 0,
          reb: g.reb || 0,
          ast: g.ast || 0,
          stl: g.stl || 0,
          blk: g.blk || 0,
          min: g.min || '0',
        }));
      //console.log('Sorted games:', sorted.length);
      setPropHistoryData(sorted);
    } catch(e) {
      //console.log('Prop history error:', e?.message, e?.response?.status);
    }
    setPropHistoryLoading(false);
  };

  const calcROIData = (betList, timeRange, unit, unitSize) => {
    if(!betList || betList.length === 0) return [];
    const settled = betList.filter(b => b.result === 'Win' || b.result === 'Loss' || b.result === 'Push');
     const sorted = [...settled].sort((a,b) => {
  const indexA = bets.indexOf(a);
  const indexB = bets.indexOf(b);
  return indexB - indexA;
});
    const filtered = timeRange === 'last10' ? sorted.slice(-10) : timeRange === 'last30' ? sorted.slice(-30) : sorted;
    let cumulative = 0;
    return filtered.map((bet, i) => {
      const betUnits = parseFloat(bet.units) || 1;
      const odds = parseInt(bet.odds) || -110;
      let profit = 0;
      if(bet.result === 'Win') {
        profit = odds > 0 ? betUnits * (odds/100) : betUnits * (100/Math.abs(odds));
      } else if(bet.result === 'Loss') {
        profit = -betUnits;
      }
      cumulative += profit;
      const displayValue = unit === 'dollars' ? cumulative * (unitSize||25) : cumulative;
      return {
        index: i+1,
        date: bet.date ? (isNaN(new Date(bet.date).getTime()) ? bet.date : new Date(bet.date).toLocaleDateString('en-US',{month:'numeric',day:'numeric'})) : `Bet ${i+1}`,
        value: parseFloat(displayValue.toFixed(2)),
        profit: parseFloat((unit==='dollars' ? profit*(unitSize||25) : profit).toFixed(2)),
        result: bet.result,
        pick: bet.pick,
      };
    });
  };

  const calcSportBreakdown = (betList, unit, unitSize) => {
    if(!betList || betList.length === 0) return [];
    const sports = {};
    betList.filter(b => b.result==='Win'||b.result==='Loss'||b.result==='Push').forEach(bet => {
      const sport = bet.sport || 'Other';
      if(!sports[sport]) sports[sport] = {sport, wins:0, losses:0, pushes:0, units:0};
      const betUnits = parseFloat(bet.units)||1;
      const odds = parseInt(bet.odds)||-110;
      let profit = 0;
      if(bet.result==='Win') { sports[sport].wins++; profit = odds>0 ? betUnits*(odds/100) : betUnits*(100/Math.abs(odds)); }
      else if(bet.result==='Loss') { sports[sport].losses++; profit = -betUnits; }
      else { sports[sport].pushes++; }
      sports[sport].units += profit;
    });
    return Object.values(sports).map(s => ({
      ...s,
      total: s.wins+s.losses+s.pushes,
      winRate: s.wins+s.losses > 0 ? Math.round((s.wins/(s.wins+s.losses))*100) : 0,
      displayUnits: unit==='dollars' ? parseFloat((s.units*(unitSize||25)).toFixed(2)) : parseFloat(s.units.toFixed(2)),
    })).sort((a,b) => b.total-a.total);
  };

  const fetchHRWatch = async () => {
    setHrWatchLoading(true);
    try {
      const today = new Date();
      const dateStr = today.getFullYear() + '-' + String(today.getMonth()+1).padStart(2,'0') + '-' + String(today.getDate()).padStart(2,'0');
      const { data, error } = await supabase
        .from('mlb_hr_watch')
        .select('*')
        .eq('game_date', dateStr)
        .order('score', { ascending: false })
        .limit(10);
      if(error) throw error;
      // Transform to match expected shape used in UI
      const candidates = (data || []).map((row: any) => ({
        player: row.player_name,
        team: row.team,
        homeTeam: row.home_team,
        hr: row.hr,
        pa: row.pa,
        hrRate: row.hr_rate,
        ba: row.ba,
        oppPitcher: row.opp_pitcher,
        oppXera: row.opp_xera,
        oppHardHit: row.opp_hard_hit,
        oppBarrel: row.opp_barrel,
        venue: row.venue,
        parkFactor: row.park_factor,
        temp: row.temp,
        windOut: row.wind_out,
        windSpeed: row.wind_speed,
        windDir: row.wind_dir,
        score: row.score,
        contactScore: row.contact_score,
        game: row.matchup,
        isFallback: row.is_fallback,
      }));
      setHrWatch(candidates);
    } catch(e) {
      console.log('HR Watch fetch error:', e);
      setHrWatch([]);
    }
    setHrWatchLoading(false);
  };

  const fetchPropOfDay = async () => {
    const _now = new Date();
    const today = _now.getFullYear() + '-' + String(_now.getMonth()+1).padStart(2,'0') + '-' + String(_now.getDate()).padStart(2,'0');

    // Check cache
    try {
      const { data: cached } = await supabase
        .from('jerry_cache')
        .select('data, fetched_at')
        .eq('cache_key', `prop_of_day_${today}`)
        .single();
      if(cached) {
        const ageHrs = (Date.now() - new Date(cached.fetched_at).getTime()) / 3600000;
        if(ageHrs < 4) { setPropOfDay(cached.data); return; }
      }
    } catch(e) {}

    setPropOfDayLoading(true);
    try {
      // Get today's A-grade props
      const { data: aGrades } = await supabase
        .from('prop_grades')
        .select('*')
        .eq('grade', 'A')
        .gte('created_at', today)
        .order('ev', {ascending: false})
        .limit(20);

      if(!aGrades || aGrades.length === 0) { setPropOfDayLoading(false); return; }

      // Score each prop with matchup context
      const scored = aGrades.map(prop => {
        let matchupScore = 0;
        const signals = [];
        const market = (prop.market || '').toLowerCase();

        // MLB pitcher strikeouts
        if(market.includes('strikeout') || market.includes('k')) {
          const mlbCtx = mlbGameContext[prop.game?.split(' @ ')?.[1]?.trim()] ||
            Object.values(mlbGameContext).find((c: any) => prop.game?.includes(c.home_team?.split(' ').pop()));
          if(mlbCtx) {
            const isHome = mlbCtx.home_pitcher?.toLowerCase().includes(prop.player?.split(' ').pop()?.toLowerCase());
            const kGap = isHome ? mlbCtx.home_k_gap : mlbCtx.away_k_gap;
            const rawXera = isHome ? parseFloat(mlbCtx.home_sp_xera) : parseFloat(mlbCtx.away_sp_xera);
            const xera = rawXera && rawXera <= 6.5 ? rawXera : NaN;
            if(kGap && kGap > 8) { matchupScore += 25; signals.push(`K gap +${kGap}pts`); }
            else if(kGap && kGap > 5) { matchupScore += 15; signals.push(`K gap +${kGap}pts`); }
            if(xera && xera < 3.5) { matchupScore += 20; signals.push(`xERA ${xera.toFixed(2)}`); }
            else if(xera && xera < 4.0) { matchupScore += 10; signals.push(`xERA ${xera.toFixed(2)}`); }
            if(mlbCtx.umpire_note?.includes('K-friendly')) { matchupScore += 20; signals.push('K-friendly ump'); }
            if(mlbCtx.temperature && mlbCtx.temperature < 55) { matchupScore += 10; signals.push(`${mlbCtx.temperature}°F cold`); }
          }
        }

        // MLB batter hits/total bases
        if(market.includes('hit') || market.includes('total base')) {
          const mlbCtx = mlbGameContext[prop.game?.split(' @ ')?.[1]?.trim()] ||
            Object.values(mlbGameContext).find((c: any) => prop.game?.includes(c.home_team?.split(' ').pop()));
          if(mlbCtx) {
            const isHome = mlbCtx.home_lineup?.toLowerCase().includes(prop.player?.split(' ').pop()?.toLowerCase());
            const platoon = isHome ? mlbCtx.home_platoon_advantage : mlbCtx.away_platoon_advantage;
            const wrcPlus = isHome ? mlbCtx.home_wrc_plus : mlbCtx.away_wrc_plus;
            if(platoon && platoon > 3) { matchupScore += 30; signals.push(`Platoon +${platoon}pts`); }
            else if(platoon && platoon > 1) { matchupScore += 15; signals.push(`Platoon +${platoon}pts`); }
            if(wrcPlus && wrcPlus > 110) { matchupScore += 15; signals.push(`wRC+ ${wrcPlus}`); }
            if(mlbCtx.park_run_factor && mlbCtx.park_run_factor > 105) { matchupScore += 15; signals.push(`Park factor ${mlbCtx.park_run_factor}`); }
          }
        }

        // NBA props
        if(prop.sport === 'NBA') {
          const teamData = Object.values(nbaTeamData).find((t: any) =>
            prop.game?.includes(t.team?.split(' ').pop())
          ) as any;
          if(teamData) {
            if(teamData.pace && teamData.pace > 100) { matchupScore += 20; signals.push(`Pace ${teamData.pace.toFixed(0)}`); }
            if(teamData.defensive_rating && teamData.defensive_rating > 115) { matchupScore += 25; signals.push(`Opp DefRtg ${teamData.defensive_rating.toFixed(0)}`); }
            if(teamData.opp_efg_pct && teamData.opp_efg_pct > 52) { matchupScore += 20; signals.push(`Opp eFG ${teamData.opp_efg_pct.toFixed(1)}%`); }
          }
        }

        matchupScore = Math.min(100, matchupScore);
        const combinedScore = (prop.ev * 0.4) + (matchupScore * 0.4) + 20;
        return { ...prop, matchupScore, combinedScore, signals };
      });

      // Pick highest combined score
      scored.sort((a, b) => b.combinedScore - a.combinedScore);
      const best = scored.find(p => p.combinedScore >= 50) || scored[0];
      if(!best) { setPropOfDayLoading(false); return; }

      // Generate Jerry narrative
      let narrative = '';
      try {
        const aiResp = await fetch('https://api.anthropic.com/v1/messages', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01',
          },
          body: JSON.stringify({
            model: 'claude-haiku-4-5-20251001',
            max_tokens: 120,
            messages: [{
              role: 'user',
              content: `You are Jerry. This is today's single best prop — the one with the deepest analytical edge.\n\nPlayer: ${best.player}\nMarket: ${best.market} ${best.best_side} \nEV: ${best.ev?.toFixed(1)}%\nMatchup signals: ${best.signals.join(', ') || 'Strong EV edge'}\nGame: ${best.game}\n\nWrite 2 sentences MAX explaining WHY this prop has edge. Reference the specific matchup data. End with the specific play. Never say 'bet'. Sound like a sharp friend who found real value.`
            }]
          })
        });
        const aiData = await aiResp.json();
        narrative = aiData?.content?.[0]?.text || 'Jerry sees real value here based on the matchup data.';
      } catch(e) {
        narrative = 'Jerry sees real value here based on the matchup data.';
      }

      const result = {
        player: best.player,
        market: best.market,
        side: best.best_side,
        line: best.best_odds,
        ev: best.ev,
        game: best.game,
        book: best.book,
        sport: best.sport,
        signals: best.signals,
        matchupScore: best.matchupScore,
        combinedScore: best.combinedScore,
        narrative,
      };

      // Cache
      try {
        await supabase.from('jerry_cache').upsert({
          cache_key: `prop_of_day_${today}`,
          data: result,
          fetched_at: new Date().toISOString(),
        }, { onConflict: 'cache_key' });
      } catch(e) {}

      setPropOfDay(result);
    } catch(e) {}
    setPropOfDayLoading(false);
  };

  const fetchPipelineMLBProps = async () => {
    setPipelineMLBLoading(true);
    try {
      const today = new Date().toISOString().split('T')[0];
      const { data, error } = await supabase
        .from('mlb_pipeline_props')
        .select('*')
        .eq('game_date', today)
        .order('conviction', { ascending: false });
      if (error) {
        console.log('Pipeline MLB props fetch error:', error.message);
        setPipelineMLBProps([]);
      } else {
        setPipelineMLBProps(data || []);
      }
    } catch (e) {
      console.log('Pipeline MLB props exception:', e?.message);
      setPipelineMLBProps([]);
    }
    setPipelineMLBLoading(false);
  };

  const fetchPropJerry = async (sport=propJerrySport) => {
    // MLB now uses pipeline-driven props (server-generated, proprietary signals)
    if (sport === 'MLB') {
      await fetchPipelineMLBProps();
      return;
    }

setPropJerryLoading(true);
    setPropJerryData([]);
    try {
      const sportKey = SPORT_KEYS[sport];
      if(!sportKey) { setPropJerryLoading(false); return; }

      // Load cache first — check Supabase then AsyncStorage
      try {
        const { data: supabaseCache } = await supabase
          .from('prop_jerry_cache')
          .select('data, fetched_at')
          .eq('sport', sport)
          .single();
        if(supabaseCache) {
          const ageMin = (Date.now() - new Date(supabaseCache.fetched_at).getTime()) / 60000;
          if(ageMin < 120) {
            setPropJerryData(supabaseCache.data);
            setPropJerryLastUpdate(new Date(supabaseCache.fetched_at));
            setPropJerryLoading(false);
            return;
          }
        }
      } catch(e) {}
      try {
        const cached = await AsyncStorage.getItem(PROP_JERRY_CACHE_KEY+'_'+sport);
        if(cached) {
          const {data, timestamp} = JSON.parse(cached);
          if(data && Date.now() - timestamp < 15*60*1000) {
            setPropJerryData(data);
            setPropJerryLastUpdate(new Date(timestamp));
            setPropJerryLoading(false);
            return;
          }
        }
      } catch(e) {}
     
      // ── PRE-SCAN: Identify strong matchup signals from pipeline data BEFORE pulling odds ──
      const matchupFlags: Record<string, {type: string, signal: string, conviction: number}[]> = {};

      if(sport === 'MLB' && Object.keys(mlbGameContext).length > 0) {
        const seen = new Set();
        Object.values(mlbGameContext).forEach((ctx: any) => {
          if(!ctx.game_id || seen.has(ctx.game_id)) return;
          seen.add(ctx.game_id);
          const gameKey = `${ctx.away_team} @ ${ctx.home_team}`;
          const flags: {type: string, signal: string, conviction: number}[] = [];

          const homeXera = ctx.home_sp_xera ? parseFloat(ctx.home_sp_xera) : null;
          const awayXera = ctx.away_sp_xera ? parseFloat(ctx.away_sp_xera) : null;
          const homeWrc = ctx.home_wrc_plus ? parseFloat(ctx.home_wrc_plus) : null;
          const awayWrc = ctx.away_wrc_plus ? parseFloat(ctx.away_wrc_plus) : null;
          const homeBpEra = ctx.home_bullpen_era ? parseFloat(ctx.home_bullpen_era) : null;
          const awayBpEra = ctx.away_bullpen_era ? parseFloat(ctx.away_bullpen_era) : null;
          const homeKGap = ctx.home_k_gap ? parseFloat(ctx.home_k_gap) : null;
          const awayKGap = ctx.away_k_gap ? parseFloat(ctx.away_k_gap) : null;

          // ── BATTER-FAVORABLE FLAGS ──
          // Away batters face home pitcher
          if(homeXera && homeXera > 4.5 && awayWrc && awayWrc > 100) {
            flags.push({type: 'batter_hits', signal: `vs ${ctx.home_pitcher} xERA ${homeXera} + wRC+ ${awayWrc}`, conviction: 15});
            if(homeBpEra && homeBpEra > 4.5) {
              flags.push({type: 'batter_total_bases', signal: `weak pitcher + bullpen ERA ${homeBpEra}`, conviction: 20});
            }
          }
          // Home batters face away pitcher
          if(awayXera && awayXera > 4.5 && homeWrc && homeWrc > 100) {
            flags.push({type: 'batter_hits', signal: `vs ${ctx.away_pitcher} xERA ${awayXera} + wRC+ ${homeWrc}`, conviction: 15});
            if(awayBpEra && awayBpEra > 4.5) {
              flags.push({type: 'batter_total_bases', signal: `weak pitcher + bullpen ERA ${awayBpEra}`, conviction: 20});
            }
          }

          // ── UNKNOWN PITCHER = BATTER FAVORABLE ──
          // Missing starter often means bullpen day or late scratch — offense benefits
          if(!ctx.home_pitcher && awayWrc && awayWrc > 95) {
            flags.push({type: 'batter_hits', signal: `vs unknown/bullpen starter + wRC+ ${awayWrc}`, conviction: 12});
          }
          if(!ctx.away_pitcher && homeWrc && homeWrc > 95) {
            flags.push({type: 'batter_hits', signal: `vs unknown/bullpen starter + wRC+ ${homeWrc}`, conviction: 12});
          }
          // Null xERA but pitcher named = early season unknown — moderate signal
          if(ctx.home_pitcher && !homeXera && awayWrc && awayWrc > 100) {
            flags.push({type: 'batter_hits', signal: `vs ${ctx.home_pitcher} (no xERA data) + wRC+ ${awayWrc}`, conviction: 10});
          }
          if(ctx.away_pitcher && !awayXera && homeWrc && homeWrc > 100) {
            flags.push({type: 'batter_hits', signal: `vs ${ctx.away_pitcher} (no xERA data) + wRC+ ${homeWrc}`, conviction: 10});
          }

          // ── STRONG OFFENSE STANDALONE ──
          // Elite offense regardless of opposing pitcher — wRC+ 110+ is top tier
          if(homeWrc && homeWrc >= 115) {
            flags.push({type: 'batter_hits', signal: `${ctx.home_team} wRC+ ${homeWrc} (elite offense)`, conviction: 12});
          }
          if(awayWrc && awayWrc >= 115) {
            flags.push({type: 'batter_hits', signal: `${ctx.away_team} wRC+ ${awayWrc} (elite offense)`, conviction: 12});
          }

          // ── STRIKEOUT-FAVORABLE ──
          if(homeKGap && homeKGap > 8) {
            flags.push({type: 'pitcher_strikeouts', signal: `${ctx.home_pitcher} K gap +${homeKGap}pts`, conviction: 20});
          }
          if(awayKGap && awayKGap > 8) {
            flags.push({type: 'pitcher_strikeouts', signal: `${ctx.away_pitcher} K gap +${awayKGap}pts`, conviction: 20});
          }

          // ── PARK + WEATHER BOOST ──
          if(ctx.park_run_factor && ctx.park_run_factor >= 108 && ctx.temperature && ctx.temperature >= 75) {
            flags.push({type: 'batter_home_runs', signal: `Park ${ctx.park_run_factor} + ${ctx.temperature}°F`, conviction: 10});
          }
          // Cold weather = pitcher advantage = K prop boost
          if(ctx.temperature && ctx.temperature <= 45) {
            flags.push({type: 'pitcher_strikeouts', signal: `${ctx.temperature}°F cold — offense suppressed`, conviction: 8});
          }

          if(flags.length > 0) {
            matchupFlags[gameKey] = flags;
          }
        });
        if(Object.keys(matchupFlags).length > 0) {
          console.log(`[PropJerry MLB] Pre-scan flagged ${Object.keys(matchupFlags).length} games with matchup signals`);
        }
      }

      // NBA pre-scan — flag games with exploitable defensive matchups
      if(sport === 'NBA' && Object.keys(nbaTeamData).length > 0) {
        const nbaList = Object.values(nbaTeamData) as any[];
        // Check each potential game matchup
        for(const team of nbaList) {
          // Find opponent by checking today's events later — for now flag team-level signals
          const defRtg = parseFloat(team.defensive_rating) || 0;
          const pace = parseFloat(team.pace) || 0;
          const oppEfg = parseFloat(team.opp_efg_pct) || 0;
          const teamName = team.team || '';

          const flags: {type: string, signal: string, conviction: number}[] = [];

          // Bad defense = points prop heaven for opponents
          if(defRtg >= 116) {
            flags.push({type: 'player_points', signal: `vs ${teamName.split(' ').pop()} DefRtg ${defRtg.toFixed(0)} (bottom 5)`, conviction: 20});
            flags.push({type: 'player_assists', signal: `vs ${teamName.split(' ').pop()} porous D — open looks`, conviction: 10});
          } else if(defRtg >= 114) {
            flags.push({type: 'player_points', signal: `vs ${teamName.split(' ').pop()} DefRtg ${defRtg.toFixed(0)} (weak)`, conviction: 10});
          }

          // High pace = more possessions = more counting stats
          if(pace >= 102) {
            flags.push({type: 'player_points', signal: `${teamName.split(' ').pop()} pace ${pace.toFixed(0)} (fast)`, conviction: 10});
            flags.push({type: 'player_rebounds', signal: `${teamName.split(' ').pop()} pace ${pace.toFixed(0)} — more possessions`, conviction: 15});
          }

          // Porous defense = assists
          if(oppEfg >= 53) {
            flags.push({type: 'player_assists', signal: `vs ${teamName.split(' ').pop()} opp eFG ${oppEfg.toFixed(1)}% (porous)`, conviction: 15});
          }

          // Elite defense = under props for opponents
          if(defRtg <= 108) {
            flags.push({type: 'player_points_under', signal: `vs ${teamName.split(' ').pop()} DefRtg ${defRtg.toFixed(0)} (elite)`, conviction: 15});
          }

          if(flags.length > 0) {
            // Key by team name — will match against game matchups when props come in
            matchupFlags[teamName] = flags;
          }
        }
        const nbaFlagged = Object.keys(matchupFlags).filter(k => !k.includes('@')).length;
        if(nbaFlagged > 0) {
          console.log(`[PropJerry NBA] Pre-scan flagged ${nbaFlagged} teams with defensive exploits`);
        }
      }

      const markets = sport==='NBA' ?
  'player_points,player_rebounds,player_assists,player_threes' :
  sport==='NFL' ? 'player_pass_yds,player_rush_yds,player_reception_yds,player_receptions,player_anytime_td' :
  sport==='NHL' ? 'player_goals,player_assists,player_shots_on_goal' :
  sport==='MLB' ? 'batter_hits,batter_total_bases,batter_rbis,batter_runs_scored,pitcher_strikeouts,batter_strikeouts,batter_home_runs' :
  sport==='UFC' ? 'fighter_total_rounds,fighter_ko_tko,fighter_decision,fighter_method_of_victory' :
  'player_points,player_rebounds,player_assists';

      const resp = await axios.get(`https://api.the-odds-api.com/v4/sports/${sportKey}/events`, {
        params: {apiKey: ODDS_API_KEY, dateFormat: 'iso'}
      });
      // Filter to today's games only (within next 16 hours to catch late west coast starts)
      const now = new Date();
      const cutoff = now.getTime() + 16 * 60 * 60 * 1000;
      const events = (resp.data || []).filter(e => {
        const t = new Date(e.commence_time).getTime();
        return t >= now.getTime() - 3600000 && t <= cutoff; // include games started up to 1hr ago
      });
      if(!events.length) { setPropJerryLoading(false); return; }

      // Sort events — flagged matchups first so they're guaranteed in the 15-event window
      const scoredEvents = events.map(event => {
        const gameKey = `${event.away_team} @ ${event.home_team}`;
        const gameFlags = matchupFlags[gameKey] || [];
        // For NBA, check team-level flags too
        let teamFlagScore = 0;
        if(sport === 'NBA') {
          for(const teamName of Object.keys(matchupFlags)) {
            if(!teamName.includes('@') && (event.home_team.includes(teamName.split(' ').pop() || '') || event.away_team.includes(teamName.split(' ').pop() || ''))) {
              teamFlagScore += matchupFlags[teamName].reduce((s: number, f: any) => s + f.conviction, 0);
            }
          }
        }
        const totalConviction = gameFlags.reduce((s, f) => s + f.conviction, 0) + teamFlagScore;
        return { ...event, _conviction: totalConviction };
      });
      scoredEvents.sort((a, b) => b._conviction - a._conviction);
      if(scoredEvents[0]._conviction > 0) {
        console.log(`[PropJerry] Events sorted — top: ${scoredEvents[0].away_team} @ ${scoredEvents[0].home_team} (conviction ${scoredEvents[0]._conviction})`);
      }

      const propMap = {};

      await Promise.all(scoredEvents.slice(0,15).map(async event => {
        try {
          const propResp = await axios.get(`https://api.the-odds-api.com/v4/sports/${sportKey}/events/${event.id}/odds`, {
            params: {
              apiKey: ODDS_API_KEY,
              regions: 'us,us2',
              markets,
              oddsFormat: 'american',
            }
          });
          const bookmakers = propResp.data?.bookmakers || [];
          bookmakers.forEach(bm => {
            bm.markets?.forEach(mkt => {
              mkt.outcomes?.forEach(outcome => {
                if(!outcome.description) return;
                const key = `${outcome.description}_${mkt.key}`;
                if(!propMap[key]) {
                  propMap[key] = {
                    player: outcome.description,
                    market: mkt.key,
                    marketLabel: mkt.key.replace('player_','').replace(/_/g,' ').toUpperCase(),
                    game: `${event.away_team} @ ${event.home_team}`,
                    gameName: `${stripMascot(event.away_team)} @ ${stripMascot(event.home_team)}`,
                    commenceTime: event.commence_time,
                    lines: [],
                    overLines: [],
                    underLines: [],
                  };
                }
                const line = outcome.point;
                const odds = outcome.price;
                const nm = outcome.name?.toLowerCase() || '';
                const side = (nm.includes('over') || nm === 'yes') ? 'over' : (nm.includes('under') || nm === 'no') ? 'under' : null;
                if(!side) return; // skip outcomes that aren't over/under
                if(side==='over') propMap[key].overLines.push({book: BOOKMAKER_MAP[bm.key]||bm.key, line, odds});
                else propMap[key].underLines.push({book: BOOKMAKER_MAP[bm.key]||bm.key, line, odds});
                propMap[key].lines.push({book: BOOKMAKER_MAP[bm.key]||bm.key, line, odds, side});
              });
            });
          });
        } catch(e) {}
      }));

      // Grade each prop — take top props distributed across games, max 5 per game
      // Pre-filter: only keep props with reasonable odds (skip extreme longshots)
const validProps = Object.values(propMap).filter(p => {
  if(!p.overLines.length || !p.underLines.length) return false;
  // Only keep parlay-friendly props: at least one side between -300 and +150
  const bestOverOdds = Math.max(...p.overLines.map(l => l.odds));
  const bestUnderOdds = Math.max(...p.underLines.map(l => l.odds));
  const overActionable = bestOverOdds >= -300 && bestOverOdds <= 150;
  const underActionable = bestUnderOdds >= -300 && bestUnderOdds <= 150;
  return overActionable || underActionable;
});
const propsByGame = {};
validProps.forEach(prop => {
  const game = prop.gameName;
  if(!propsByGame[game]) propsByGame[game] = [];
  propsByGame[game].push(prop);
});
// Sort each game's props so actionable markets come first
const marketPriority = {
  // NHL
  'player_shots_on_goal':0, 'player_assists':1, 'player_goals':2,
  // MLB — strikeouts and hits are parlay-friendly, HRs/RBIs are longshots
  'pitcher_strikeouts':0, 'batter_hits':0, 'batter_total_bases':1, 'batter_strikeouts':1,
  'batter_runs_scored':2, 'batter_rbis':2, 'batter_home_runs':3,
};
Object.values(propsByGame).forEach(gameProps => {
  gameProps.sort((a,b) => (marketPriority[a.market]??99) - (marketPriority[b.market]??99));
});
const propEntries = Object.values(propsByGame)
  .flatMap(gameProps => gameProps.slice(0, 5))
  .slice(0, 60);
const gradedRaw = [];
for(let pi = 0; pi < propEntries.length; pi++) {
  const prop = propEntries[pi];
  if(pi > 0) await new Promise(r => setTimeout(r, 150));
  gradedRaw.push(await (async (prop) => {
        const overOdds = prop.overLines.map(l=>l.odds);
        const underOdds = prop.underLines.map(l=>l.odds);
        if(!overOdds.length || !underOdds.length) return null;

        // Use median odds for true probability (resists outliers better than average)
        const median = (arr: number[]) => {
          const s = [...arr].sort((a,b)=>a-b);
          const mid = Math.floor(s.length/2);
          return s.length%2 ? s[mid] : (s[mid-1]+s[mid])/2;
        };
        const medOverOdds = median(overOdds);
        const medUnderOdds = median(underOdds);

        // Best lines — only consider parlay-friendly odds (-300 to +150)
        const actionableOver = prop.overLines.filter(l => l.odds >= -300 && l.odds <= 150);
        const actionableUnder = prop.underLines.filter(l => l.odds >= -300 && l.odds <= 150);
        const bestOver = actionableOver.length ? actionableOver.reduce((best,l) => l.odds > (best?.odds||-9999) ? l : best, null) : null;
        const bestUnder = actionableUnder.length ? actionableUnder.reduce((best,l) => l.odds > (best?.odds||-9999) ? l : best, null) : null;
        if(!bestOver && !bestUnder) return null;

        // EV calculation using median for true probability
        const overProb = medOverOdds < 0 ? Math.abs(medOverOdds)/(Math.abs(medOverOdds)+100) : 100/(medOverOdds+100);
        const underProb = medUnderOdds < 0 ? Math.abs(medUnderOdds)/(Math.abs(medUnderOdds)+100) : 100/(medUnderOdds+100);
        const vigFree = overProb + underProb;
        const vfOver = overProb/vigFree;
        const vfUnder = underProb/vigFree;

        const bestOverEV = bestOver ? (bestOver.odds > 0 ? (bestOver.odds/100)*vfOver - (1-vfOver) : (100/Math.abs(bestOver.odds))*vfOver - (1-vfOver)) * 100 : -99;
        const bestUnderEV = bestUnder ? (bestUnder.odds > 0 ? (bestUnder.odds/100)*vfUnder - (1-vfUnder) : (100/Math.abs(bestUnder.odds))*vfUnder - (1-vfUnder)) * 100 : -99;
       
        const bestEV = Math.max(bestOverEV, bestUnderEV);
        const bestSide = bestOverEV >= bestUnderEV ? 'Over' : 'Under';
        const bestLine = bestSide==='Over' ? bestOver : bestUnder;
        // Line consensus
        const overLines = prop.overLines.map(l=>l.line);
        const lineRange = overLines.length>1 ? Math.max(...overLines)-Math.min(...overLines) : 0;
        const bookCount = new Set(prop.lines.map(l=>l.book)).size;
       
        // Grade
        let grade, gradeColor, Jerry;
       const playerFirst = prop.player.split(' ')[0];
        const aQuotes = [
          `🎤 Jerry's radar is going off on ${playerFirst} ${bestSide} ${bestLine?.line} — ${bestEV.toFixed(1)}% edge with ${bookCount} books confirming. Number looks soft, worth a look before it moves.`,
          `🎤 ${playerFirst} ${bestSide} ${bestLine?.line} has Jerry's full attention. Tight consensus across ${bookCount} books and the value is there. Do your homework.`,
          `🎤 Strong edge detected — ${playerFirst} ${bestSide} ${bestLine?.line} at ${bestEV.toFixed(1)}% EV. Books are in rare agreement here. Jerry sees real value.`,
        ];
        const bQuotes = [
          `🎤 Jerry likes ${playerFirst} ${bestSide} ${bestLine?.line}. ${bestEV.toFixed(1)}% EV across ${bookCount} books — solid value, worth monitoring before it tightens.`,
          `🎤 ${playerFirst} ${bestSide} ${bestLine?.line} has Jerry's attention. Not the loudest signal but the math checks out. Good spot at ${bestLine?.book}.`,
          `🎤 ${bookCount} books, ${bestEV.toFixed(1)}% edge — ${playerFirst} ${bestSide} ${bestLine?.line} looks interesting. Jerry approves of the value.`,
        ];
        const cQuotes = [
          `🎤 Mild edge on ${playerFirst} ${bestSide} ${bestLine?.line}. ${bestEV.toFixed(1)}% EV — worth tracking if you like the spot.`,
          `🎤 Jerry sees something on ${playerFirst} ${bestSide} ${bestLine?.line} but it's faint. Monitor the line movement.`,
          `🎤 ${playerFirst} ${bestSide} ${bestLine?.line} — there's an edge but Jerry's not loud about it. Proceed with your own judgment.`,
        ];
        const dQuotes = [
          `🎤 Jerry passes on ${playerFirst} ${bestLine?.line}. Books are sharp here — no meaningful edge in the data today.`,
          `🎤 ${playerFirst} ${bestSide} ${bestLine?.line}? The number's too sharp. Jerry steps aside.`,
          `🎤 Negative EV on ${playerFirst} — the market's efficient here. Nothing to exploit today.`,
        ];

           // Model probability — independent signal from BDL/pipeline data
        let modelProb = null;
        let modelAvg = null;
        let modelSignal = '';

        // NBA — use BDL weighted averages + opponent defense
        if(propJerrySport === 'NBA') {
          try {
            const stats = await fetchBDLPlayerStats(prop.player);
            if(stats) {
              const market = prop.market;
              const line = parseFloat(bestLine?.line || bestOver?.line || bestUnder?.line);
              // Use recency-weighted average instead of straight average
              const weighted = stats.weighted || stats.last5;
              if(market.includes('points')) modelAvg = parseFloat(weighted.pts);
              else if(market.includes('rebounds')) modelAvg = parseFloat(weighted.reb);
              else if(market.includes('assists')) modelAvg = parseFloat(weighted.ast);

              // Opponent defensive adjustment
              let oppDefAdj = 0;
              let oppDefSignal = '';
              const gameTeams = prop.game?.split(' @ ') || [];
              const oppTeamName = gameTeams[0]?.trim() || gameTeams[1]?.trim();
              if(oppTeamName) {
                const oppTeam = Object.values(nbaTeamData).find((t: any) =>
                  oppTeamName.includes(t.team?.split(' ').pop()) || t.team?.includes(oppTeamName.split(' ').pop())
                ) as any;
                if(oppTeam) {
                  const defRtg = parseFloat(oppTeam.defensive_rating);
                  const oppEFG = parseFloat(oppTeam.opp_efg_pct);
                  const pace = parseFloat(oppTeam.pace);
                  if(market.includes('points') && defRtg) {
                    // Bad defense (high DefRtg) → boost over, good defense → boost under
                    if(defRtg >= 115) { oppDefAdj = 0.04; oppDefSignal = `vs weak defense (DefRtg ${defRtg.toFixed(1)})`; }
                    else if(defRtg >= 112) { oppDefAdj = 0.02; oppDefSignal = `vs below avg defense (DefRtg ${defRtg.toFixed(1)})`; }
                    else if(defRtg <= 108) { oppDefAdj = -0.04; oppDefSignal = `vs elite defense (DefRtg ${defRtg.toFixed(1)})`; }
                    else if(defRtg <= 110) { oppDefAdj = -0.02; oppDefSignal = `vs good defense (DefRtg ${defRtg.toFixed(1)})`; }
                  } else if(market.includes('rebounds')) {
                    // High pace → more possessions → more rebound opportunities
                    if(pace && pace >= 101) { oppDefAdj = 0.02; oppDefSignal = `vs fast pace team (${pace.toFixed(1)})`; }
                    else if(pace && pace <= 97) { oppDefAdj = -0.02; oppDefSignal = `vs slow pace team (${pace.toFixed(1)})`; }
                  } else if(market.includes('assists')) {
                    // Bad defense → more open looks → more assists
                    if(oppEFG && oppEFG >= 52) { oppDefAdj = 0.03; oppDefSignal = `vs porous defense (opp eFG ${oppEFG.toFixed(1)}%)`; }
                    else if(oppEFG && oppEFG <= 48) { oppDefAdj = -0.03; oppDefSignal = `vs stingy defense (opp eFG ${oppEFG.toFixed(1)}%)`; }
                  }
                }
              }

              if(modelAvg !== null && !isNaN(modelAvg) && line > 0) {
                const gap = ((modelAvg - line) / line) * 100;
                const straightAvg = market.includes('points') ? stats.last5.pts : market.includes('rebounds') ? stats.last5.reb : stats.last5.ast;
                const recencyNote = Math.abs(modelAvg - parseFloat(straightAvg)) >= 1.5 ? ` (recency-weighted, straight avg ${straightAvg})` : '';

                if(bestSide === 'Over') {
                  if(gap >= 15) { modelProb = 0.62 + oppDefAdj; modelSignal = `Weighted avg ${modelAvg} vs line ${line} (+${gap.toFixed(0)}% above)${recencyNote} ${oppDefSignal}`; }
                  else if(gap >= 8) { modelProb = 0.56 + oppDefAdj; modelSignal = `Weighted avg ${modelAvg} vs line ${line} (+${gap.toFixed(0)}% above)${recencyNote} ${oppDefSignal}`; }
                  else if(gap <= -8) { modelProb = 0.42 + oppDefAdj; modelSignal = `Weighted avg ${modelAvg} vs line ${line} (${gap.toFixed(0)}% below — wrong side) ${oppDefSignal}`; }
                  else { modelProb = 0.50 + oppDefAdj; modelSignal = `Weighted avg ${modelAvg} near line ${line}${recencyNote} ${oppDefSignal}`; }
                } else {
                  if(gap <= -15) { modelProb = 0.62 + oppDefAdj; modelSignal = `Weighted avg ${modelAvg} vs line ${line} (${gap.toFixed(0)}% below)${recencyNote} ${oppDefSignal}`; }
                  else if(gap <= -8) { modelProb = 0.56 + oppDefAdj; modelSignal = `Weighted avg ${modelAvg} vs line ${line} (${gap.toFixed(0)}% below)${recencyNote} ${oppDefSignal}`; }
                  else if(gap >= 8) { modelProb = 0.42 + oppDefAdj; modelSignal = `Weighted avg ${modelAvg} vs line ${line} (+${gap.toFixed(0)}% above — wrong side) ${oppDefSignal}`; }
                  else { modelProb = 0.50 + oppDefAdj; modelSignal = `Weighted avg ${modelAvg} near line ${line}${recencyNote} ${oppDefSignal}`; }
                }
                // Clamp probability
                modelProb = Math.max(0.35, Math.min(0.70, modelProb));
              }
            }
          } catch(e) {}
        }

        // MLB — use pipeline data for K gap and platoon
        if(propJerrySport === 'MLB') {
          try {
            const gameTeams = prop.game?.split(' @ ') || [];
            const homeTeam = gameTeams[1]?.trim();
            const mlbCtx = homeTeam ? (mlbGameContext[homeTeam] ||
              Object.values(mlbGameContext).find((c: any) => prop.game?.includes(c.home_team?.split(' ').pop()))) : null;
            if(mlbCtx) {
              if(prop.market.includes('strikeout') || prop.marketLabel === 'PITCHER STRIKEOUTS') {
                const isHome = mlbCtx.home_pitcher?.toLowerCase().includes(prop.player.split(' ').pop()?.toLowerCase());
                const kGap = parseFloat(isHome ? mlbCtx.home_k_gap : mlbCtx.away_k_gap);
                if(!isNaN(kGap)) {
                  modelSignal = `K gap: ${kGap > 0 ? '+' : ''}${kGap}pts vs lineup`;
                  if(kGap >= 8) modelProb = 0.58;
                  else if(kGap >= 4) modelProb = 0.54;
                  else if(kGap <= -8) modelProb = 0.42;
                  else modelProb = 0.50;
                }
              }
              if(prop.market.includes('hits') || prop.market.includes('total_bases') || prop.market.includes('rbis') || prop.market.includes('runs_scored') || prop.market.includes('home_run')) {
                // Fetch individual batter stats from MLB Stats API
                const batterStats = await fetchMLBBatterStats(prop.player);
                if(batterStats && batterStats.pa >= 10) {
                  const ba = batterStats.ba || 0;
                  const kRate = batterStats.k_rate || 0;
                  const pa = batterStats.pa;
                  const isBench = batterStats.isBench;

                  if(bestSide === 'Over') {
                    // Over hits: high BA, low K rate = good
                    if(ba >= .300) { modelProb = 0.60; modelSignal = `${prop.player} BA .${Math.round(ba*1000)} in ${pa} PA — elite contact`; }
                    else if(ba >= .270) { modelProb = 0.56; modelSignal = `${prop.player} BA .${Math.round(ba*1000)} in ${pa} PA — solid contact`; }
                    else if(ba >= .240) { modelProb = 0.52; modelSignal = `${prop.player} BA .${Math.round(ba*1000)} in ${pa} PA — average`; }
                    else { modelProb = 0.45; modelSignal = `${prop.player} BA .${Math.round(ba*1000)} in ${pa} PA — weak contact`; }
                  } else {
                    // Under hits: high K rate, low PA, bench role = good for under
                    if(isBench) { modelProb = 0.58; modelSignal = `${prop.player} bench role — ${pa} PA, limited opportunities`; }
                    else if(kRate >= 30) { modelProb = 0.56; modelSignal = `${prop.player} ${kRate}% K rate — high strikeout risk`; }
                    else if(ba <= .200) { modelProb = 0.56; modelSignal = `${prop.player} BA .${Math.round(ba*1000)} — struggling`; }
                    else { modelProb = 0.48; modelSignal = `${prop.player} BA .${Math.round(ba*1000)}, ${kRate}% K — no clear under edge`; }
                  }
                } else {
                  // No individual stats — fall back to team level
                  const isHome = mlbCtx.home_lineup?.toLowerCase().includes(prop.player.split(' ').pop()?.toLowerCase());
                  const platoon = parseFloat(isHome ? mlbCtx.home_platoon_advantage : mlbCtx.away_platoon_advantage);
                  const wrcPlus = parseInt(isHome ? mlbCtx.home_wrc_plus : mlbCtx.away_wrc_plus);
                  if(!isNaN(platoon) && platoon > 3) { modelProb = 0.54; modelSignal = `Team platoon +${platoon} (no individual stats)`; }
                  else modelProb = 0.50;
                }
              }
            }
          } catch(e) {}
        }

        const isMLB = propJerrySport === 'MLB';
const isNHL = propJerrySport === 'NHL';
const minBooksA = isMLB ? 3 : isNHL ? 2 : 4;
const minBooksB = isMLB ? 2 : isNHL ? 1 : 3;
const maxRangeA = isMLB ? 0.5 : isNHL ? 1.0 : 0.5;
const maxRangeB = isMLB ? 1.0 : isNHL ? 1.5 : 1.0;

// Model confirmation gates — require independent data to back up EV edge
const hasModelEdge = (propJerrySport === 'NBA' || propJerrySport === 'MLB') ? (modelProb !== null && modelProb >= 0.55) : true;
const modelConfirmed = (propJerrySport === 'NBA' || propJerrySport === 'MLB') ? (modelProb !== null && modelProb >= 0.58) : true;

// ── MATCHUP FLAG CONVICTION BONUS ──
// If pipeline pre-scan flagged this game+market, boost EV thresholds
let matchupConviction = 0;
let matchupSignals: string[] = [];
// Check MLB game-level flags
const gameFlags = matchupFlags[prop.game] || [];
// Check NBA team-level flags — match opponent team name against the prop's game
let teamFlags: {type: string, signal: string, conviction: number}[] = [];
if(propJerrySport === 'NBA') {
  // Find which teams are in this prop's game
  const gameParts = prop.game?.split(' @ ') || [];
  for(const part of gameParts) {
    const teamMatch = Object.keys(matchupFlags).find(k => !k.includes('@') && part.includes(k.split(' ').pop() || ''));
    if(teamMatch) teamFlags = [...teamFlags, ...matchupFlags[teamMatch]];
  }
}
const allFlags = [...gameFlags, ...teamFlags];
if(allFlags.length > 0) {
  const marketLower = prop.market.toLowerCase();
  for(const flag of allFlags) {
    // Batter props: conviction only applies to OVER side (matchup = batter favorable)
    // If bestSide is Under, suppress batter conviction — contradicts the matchup signal
    const isBatterFlag = flag.type.startsWith('batter_');
    if(isBatterFlag && bestSide === 'Under') continue; // skip — conviction is for Over, not Under

    // Pitcher K props: conviction only applies to OVER strikeouts
    const isKFlag = flag.type === 'pitcher_strikeouts';
    if(isKFlag && bestSide === 'Under') continue;

    // NBA points/rebounds/assists: conviction is for Over
    const isNBAOverFlag = ['player_points', 'player_rebounds', 'player_assists'].includes(flag.type);
    if(isNBAOverFlag && bestSide === 'Under') continue;

    // NBA under flag only applies to Under side
    if(flag.type === 'player_points_under' && bestSide !== 'Under') continue;

    if(flag.type === 'batter_hits' && (marketLower.includes('hits') || marketLower.includes('total_bases') || marketLower.includes('rbis'))) {
      matchupConviction += flag.conviction;
      matchupSignals.push(flag.signal);
    } else if(flag.type === 'batter_total_bases' && (marketLower.includes('total_bases') || marketLower.includes('home_run'))) {
      matchupConviction += flag.conviction;
      matchupSignals.push(flag.signal);
    } else if(flag.type === 'pitcher_strikeouts' && marketLower.includes('strikeout')) {
      matchupConviction += flag.conviction;
      matchupSignals.push(flag.signal);
    } else if(flag.type === 'batter_home_runs' && marketLower.includes('home_run')) {
      matchupConviction += flag.conviction;
      matchupSignals.push(flag.signal);
    } else if(flag.type === 'player_points' && marketLower.includes('points')) {
      matchupConviction += flag.conviction;
      matchupSignals.push(flag.signal);
    } else if(flag.type === 'player_rebounds' && marketLower.includes('rebounds')) {
      matchupConviction += flag.conviction;
      matchupSignals.push(flag.signal);
    } else if(flag.type === 'player_assists' && marketLower.includes('assists')) {
      matchupConviction += flag.conviction;
      matchupSignals.push(flag.signal);
    } else if(flag.type === 'player_points_under' && marketLower.includes('points')) {
      matchupConviction += flag.conviction;
      matchupSignals.push(flag.signal);
    }
  }
}
// ── ODDS-BASED CONVICTION SCALING ──
// Heavy favorites are partially priced in — reduce conviction
// Underdogs with matchup signal = market disagrees = more value
if(matchupConviction > 0 && bestLine?.odds) {
  const propOdds = parseFloat(bestLine.odds);
  if(propOdds >= 100) matchupConviction += 10;       // plus odds — market underpricing
  else if(propOdds >= -150) matchupConviction += 5;   // mild favorite — value exists
  else if(propOdds <= -250) matchupConviction -= 5;   // heavy fav — mostly priced in
}

// ── DUAL-TRACK GRADING ──
const isMatchupProp = matchupConviction >= 15;

if(isMatchupProp) {
  // MATCHUP TRACK — graded on pipeline conviction, not EV
  if(matchupConviction >= 35) {
    grade='A'; gradeColor='#00e5a0';  // multiple strong signals converging
  } else if(matchupConviction >= 25) {
    grade='B'; gradeColor='#FFB800';  // solid signal
  } else if(matchupConviction >= 15) {
    grade='C'; gradeColor='#0099ff';  // moderate signal
  }
  // EV bonus — if matchup prop ALSO has strong positive EV, upgrade
  if(bestEV >= 5 && grade === 'B') { grade='A'; gradeColor='#00e5a0'; }
} else {
  // EV TRACK — line-shopping math, tightened from 1-8 A grade performance
  const pathOneA = bestEV >= 5 && bookCount >= minBooksA && lineRange <= maxRangeA && hasModelEdge;
  const pathTwoA = bestEV >= 7 && bookCount >= minBooksB && lineRange <= maxRangeB && modelConfirmed;

  if(pathOneA || pathTwoA) {
    grade='A'; gradeColor='#00e5a0';
  } else if(bestEV >= 3 && bookCount >= minBooksB && lineRange <= maxRangeB && hasModelEdge) {
    grade='B'; gradeColor='#FFB800';
  } else if(bestEV >= 1) {
    grade='C'; gradeColor='#0099ff';
  } else {
    grade='D'; gradeColor='#ff4d6d';
  }
}

// Model override — if model says wrong side or coin flip, cap grade
if((propJerrySport === 'NBA' || propJerrySport === 'MLB') && modelProb !== null && modelProb <= 0.50) {
  if(grade === 'A' || grade === 'B') { grade = 'C'; gradeColor = '#0099ff'; }
}
// NBA without BDL stats — can't confirm, cap at B
if(propJerrySport === 'NBA' && modelAvg === null && grade === 'A') {
  grade = 'B'; gradeColor = '#FFB800';
}
// Temporarily exclude pitcher K props — early season data too thin
if(prop.marketLabel === 'PITCHER STRIKEOUTS' && new Date() < new Date('2026-05-01')) {
  grade = 'C'; gradeColor = '#0099ff';
}
        // AI Jerry narration
        try {
          const gradeContext = grade==='A' 
            ? `STRONG EDGE — ${bookCount >= 4 ? 'market-confirmed across ' + bookCount + ' books' : 'high EV at ' + bestEV.toFixed(1) + '%'}. Explain the specific reason this line is mispriced.`
              : grade==='B' 
            ? `Solid edge at ${bestEV.toFixed(1)}% EV. Confident but measured.`
              : grade==='C' 
            ? `Mild edge. Cautious and analytical.`
              : `No real edge. Advise passing.`;
          //console.log('AI Jerry calling for:', prop.player, grade);
                              const aiResp = await fetch('https://api.anthropic.com/v1/messages', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'x-api-key': ANTHROPIC_API_KEY,
              'anthropic-version': '2023-06-01',
            },
            body: JSON.stringify({
              model: 'claude-haiku-4-5-20251001',
              max_tokens: 120,
              messages: [{
                role: 'user',
                content: await (async () => {
                  let kenpomContext = '';
                  let playerContext = '';
                  if(sport === 'NCAAB' && bartData.length) {
                    const gameTeam = fuzzyMatchTeam(stripMascot(prop.gameName?.split(' vs ')?.[0] || ''), bartData, 'team');
                    if(gameTeam) {
                      kenpomContext = ` Team KenPom: Rank #${gameTeam.rank||'N/A'}, AdjOE: ${gameTeam.adjOE||'N/A'}, AdjDE: ${gameTeam.adjDE||'N/A'}, SOS: ${gameTeam.sos||'N/A'}.`;
                    }
                  }
                  if(sport === 'NBA' || sport === 'NFL') {
                    const stats = await fetchBDLPlayerStats(prop.player);
                    if(stats) {
                      const MARKET_STAT_MAP = {
                        'player_points': {key:'pts', label:'pts/game'},
                        'player_rebounds': {key:'reb', label:'reb/game'},
                        'player_assists': {key:'ast', label:'ast/game'},
                        'player_threes': {key:'fg3m', label:'3pm/game'},
                        'player_blocks': {key:'blk', label:'blk/game'},
                        'player_steals': {key:'stl', label:'stl/game'},
                      };
                      const statInfo = MARKET_STAT_MAP[prop.market as keyof typeof MARKET_STAT_MAP];
                      if(statInfo && stats.last5[statInfo.key] !== undefined) {
                        playerContext = ` ${prop.player} last 5 avg: ${stats.last5[statInfo.key]} ${statInfo.label}. Line: ${bestSide} ${bestLine?.line}.`;
                      } else {
                        playerContext = ` ${prop.player} last 5 avg: ${stats.last5.pts}pts, ${stats.last5.reb}reb, ${stats.last5.ast}ast.`;
                      }
                    }
                  }
                  if(sport === 'MLB') {
                    try {
                      // Get game context for this prop's game
                      const gameTeams = prop.game?.split(' @ ') || []; // use prop.game not prop.gameName — has full team names
                      const homeTeam = gameTeams[1]?.trim()
                      const awayTeam = gameTeams[0]?.trim();
                      if(homeTeam) {
                        const { data: mlbCtx } = await supabase
                          .from('mlb_game_context')
                          .select('*')
                          .eq('home_team', homeTeam)
                          .single();
                        if(mlbCtx) {
                          // For strikeout props — pitcher K rate + umpire is key
                          const isKProp = prop.market.toLowerCase().includes('strikeout') || prop.market.toLowerCase().includes('strike');
                          const isHRProp = prop.market.toLowerCase().includes('home_run') || prop.market.toLowerCase().includes('homer');
                          const isHitsProp = prop.market.toLowerCase().includes('hits') || prop.market.toLowerCase().includes('total_bases');
                          
                          if(isKProp) {
                            // Find which pitcher this prop is for
                            const isHomePitcher = mlbCtx.home_pitcher && prop.player.toLowerCase().includes(mlbCtx.home_pitcher.split(' ').pop().toLowerCase());
                            const pitcherCtx = isHomePitcher ? 
                              `${mlbCtx.home_pitcher} (${mlbCtx.pitcher_context?.split('|')[0] || 'stats N/A'})` :
                              `${mlbCtx.away_pitcher} (${mlbCtx.pitcher_context?.split('|')[1] || 'stats N/A'})`;
                            playerContext = ` MLB K prop context: ${pitcherCtx}. Umpire: ${mlbCtx.umpire_note || 'N/A'}. Park: ${mlbCtx.venue} (run factor ${mlbCtx.park_run_factor}).`;
                          } else if(isHRProp) {
                            playerContext = ` MLB HR prop context: ${mlbCtx.venue} HR factor ${mlbCtx.park_run_factor}. Weather: ${mlbCtx.temperature}°F, wind ${mlbCtx.wind_speed}mph ${mlbCtx.wind_direction}.`;
                          } else if(isHitsProp || prop.market.includes('rbis') || prop.market.includes('runs_scored')) {
                            // Batter prop — find opposing pitcher's contact profile
                            // Use full name (not just last name) to avoid "Laureano" → matching "Laureano" who's a teammate
                            const playerFullLower = prop.player.toLowerCase();
                            const playerLast = prop.player.split(' ').pop()?.toLowerCase() || '';
                            const homeLineupLower = (mlbCtx.home_lineup || '').toLowerCase();
                            const awayLineupLower = (mlbCtx.away_lineup || '').toLowerCase();
                            const inHome = homeLineupLower.includes(playerFullLower) || homeLineupLower.includes(playerLast);
                            const inAway = awayLineupLower.includes(playerFullLower) || awayLineupLower.includes(playerLast);
                            // Only trust team assignment when UNAMBIGUOUS — one lineup, not both, not neither
                            const ambiguous = inHome === inAway; // both true OR both false
                            const isBatterHome = ambiguous ? null : inHome;
                            // Opposing pitcher is the starter the batter faces — null if we can't confidently tell
                            const oppPitcher = isBatterHome === true ? mlbCtx.away_pitcher : isBatterHome === false ? mlbCtx.home_pitcher : null;
                            let contactProfile = '';
                            if(oppPitcher) {
                              try {
                                // Validate: query by full last name and verify it matches the game's starter
                                const oppLast = oppPitcher.split(' ').pop() || '';
                                const { data: pitcherStats } = await supabase
                                  .from('mlb_pitcher_stats')
                                  .select('player_name, baa_allowed, xba_allowed, hard_hit_pct_allowed, throws')
                                  .ilike('player_name', `%${oppLast}%`)
                                  .limit(3);
                                // Find the exact match — don't use a different pitcher with the same last name
                                const exactMatch = pitcherStats?.find(p =>
                                  p.player_name?.toLowerCase().includes(oppLast.toLowerCase())
                                );
                                if(exactMatch && (exactMatch.baa_allowed || exactMatch.xba_allowed)) {
                                  contactProfile = ` Opposing pitcher ${oppPitcher} contact profile: BAA ${exactMatch.baa_allowed?.toFixed(3) || 'N/A'}, xBA ${exactMatch.xba_allowed?.toFixed(3) || 'N/A'}, hard hit% ${exactMatch.hard_hit_pct_allowed?.toFixed(1) || 'N/A'}%. Throws ${exactMatch.throws || 'R'}.`;
                                }
                              } catch(e) {}
                            }
                            // Only surface platoon/wRC+ when we're confident which team the batter is on
                            const platoon = isBatterHome === true ? mlbCtx.home_platoon_advantage : isBatterHome === false ? mlbCtx.away_platoon_advantage : null;
                            const wrcPlus = isBatterHome === true ? mlbCtx.home_wrc_plus : isBatterHome === false ? mlbCtx.away_wrc_plus : null;
                            // Fetch individual batter stats for prompt
                            let individualStats = '';
                            try {
                              const bs = await fetchMLBBatterStats(prop.player);
                              if(bs && bs.pa >= 10) {
                                individualStats = ` ${prop.player}: BA .${Math.round((bs.ba||0)*1000)}, ${bs.pa} PA, ${bs.k_rate}% K rate, OPS ${bs.ops?.toFixed(3) || 'N/A'}${bs.isBench ? ' (BENCH/PLATOON — limited role)' : ''}.`;
                              }
                            } catch(e) {}
                            playerContext = ` MLB batter prop:${individualStats} ${mlbCtx.venue} (park factor ${mlbCtx.park_run_factor}). Team wRC+ ${wrcPlus || 'N/A'}. Platoon adv: ${platoon ? '+' + platoon : 'N/A'}.${contactProfile} ${mlbCtx.umpire_note || ''}`;
                          } else {
                            playerContext = ` MLB context: ${mlbCtx.venue} (park factor ${mlbCtx.park_run_factor}), ${mlbCtx.temperature}°F. Umpire: ${mlbCtx.umpire_note || 'N/A'}.`;
                          }
                        }
                      }
                    } catch(e) {}
                  }
                  if(sport === 'UFC') {
                    try {
                      const fighterName = prop.player;
                      const { data: ufcCtx } = await supabase
                        .from('ufc_fighter_stats')
                        .select('*')
                        .ilike('fighter_name', `%${fighterName.split(' ').pop()}%`)
                        .single();

                      // Find opponent from the game matchup
                      const gameTeams = prop.game?.split(' @ ') || [];
                      const awayFighter = gameTeams[0]?.trim();
                      const homeFighter = gameTeams[1]?.trim();
                      const opponentName = fighterName === awayFighter ? homeFighter : awayFighter;
                      let oppCtx = null;
                      if(opponentName) {
                        try {
                          const { data: oppData } = await supabase
                            .from('ufc_fighter_stats')
                            .select('*')
                            .ilike('fighter_name', `%${opponentName.split(' ').pop()}%`)
                            .single();
                          oppCtx = oppData;
                        } catch(e) {}
                      }

                      if(ufcCtx) {
                        const finishingStr = ufcCtx.finishing_rate ? `${ufcCtx.finishing_rate}% finishing rate` : 'finishing rate N/A';
                        const strikingStr = ufcCtx.slpm ? `${ufcCtx.slpm} SLpM, ${ufcCtx.str_acc}% accuracy` : 'striking N/A';
                        const grapplingStr = ufcCtx.td_avg ? `${ufcCtx.td_avg} TD/15min, ${ufcCtx.td_acc}% TD acc, ${ufcCtx.sub_avg} sub/15min` : 'grappling N/A';
                        const decisionRate = ufcCtx.total_wins > 0 ? Math.round((ufcCtx.wins_by_dec / ufcCtx.total_wins) * 100) : null;

                        let oppStr = '';
                        let matchupStr = '';
                        if(oppCtx) {
                          const oppFinishing = oppCtx.finishing_rate ? `${oppCtx.finishing_rate}% finishing` : 'N/A';
                          const oppDecRate = oppCtx.total_wins > 0 ? Math.round((oppCtx.wins_by_dec / oppCtx.total_wins) * 100) : null;
                          oppStr = ` Opponent ${oppCtx.fighter_name} (${oppCtx.record || 'N/A'}): str_def ${oppCtx.str_def || 'N/A'}%, TD def ${oppCtx.td_def || 'N/A'}%, SApM ${oppCtx.sapm || 'N/A'}, ${oppFinishing}.`;

                          // Market-specific matchup analysis
                          if(prop.market.includes('method_of_victory') || prop.market.includes('ko_tko')) {
                            matchupStr = ` Matchup: ${ufcCtx.fighter_name} finishing ${ufcCtx.finishing_rate || 0}% vs ${oppCtx.fighter_name} str_def ${oppCtx.str_def || 'N/A'}% / TD def ${oppCtx.td_def || 'N/A'}%.`;
                          } else if(prop.market.includes('total_rounds') || prop.market.includes('decision')) {
                            const combinedDecRate = decisionRate !== null && oppDecRate !== null ? Math.round((decisionRate + oppDecRate) / 2) : null;
                            matchupStr = ` Rounds context: ${ufcCtx.fighter_name} goes to decision ${decisionRate ?? 'N/A'}% of wins, ${oppCtx.fighter_name} ${oppDecRate ?? 'N/A'}%. Combined decision rate: ${combinedDecRate ?? 'N/A'}%.`;
                          }
                        }

                        playerContext = ` UFC prop fighter: ${ufcCtx.fighter_name} (${ufcCtx.record || 'N/A'}). Striking: ${strikingStr}. Grappling: ${grapplingStr}. Finishing: ${finishingStr}. Stance: ${ufcCtx.stance || 'N/A'}.${oppStr}${matchupStr}`;
                      }
                    } catch(e) {}
                  }
                  const isAGrade = grade === 'A';
const isBGrade = grade === 'B';
const aGradeInstruction = isAGrade ? `
CRITICAL — This is an A grade prop. You MUST explain WHY the market is mispriced in specific statistical terms.
Structure: [What the data shows] + [Why this creates value at this line/book].
Example format: "Gore's 27% K rate faces a lineup punching out 24% of ABs — that gap is real. DraftKings has the line 0.5 Ks soft vs the consensus."
Never just say "edge detected" or "value here" — say WHY specifically.` : '';

const bookContext = bookCount >= 4 
  ? `${bookCount} books in tight consensus — this is a market-confirmed edge`
  : bookCount >= 2 
  ? `${bookCount} books posting — cross-book value confirmed`
  : `1 book posting — high EV but verify line before it moves`;

// Extract game entities for all sports (universal entity isolation)
const gameTeams = prop.game?.split(' @ ') || [];
const awayEntity = gameTeams[0]?.trim() || null;
const homeEntity = gameTeams[1]?.trim() || null;
// Subject team inference (conservative): for MLB/NBA/NFL teams are in game string.
// For UFC, subject IS the entity — their "team" is themselves, opponent is the other fighter.
// Default unknown when we can't confidently infer.
let subjectTeam = null;
let opposingEntity = null;
if(awayEntity && homeEntity) {
  if(sport === 'UFC') {
    // Fighter name IS the entity — match prop.player to home/away fighter
    subjectTeam = prop.player === awayEntity ? awayEntity : prop.player === homeEntity ? homeEntity : null;
    opposingEntity = subjectTeam === awayEntity ? homeEntity : subjectTeam === homeEntity ? awayEntity : null;
  }
  // For team sports, subject team isn't reliably in prop.player — leave null if we can't infer,
  // the ENTITY FACTS block will list both and let the LLM reason from context fields.
}

return `You are Prop Jerry, a sharp sports betting analyst for The Sweat Locker.

=== ENTITY FACTS (read first, do not confuse) ===
SUBJECT: ${prop.player}
PROP: ${bestSide} ${bestLine?.line} ${prop.market || ''}
${awayEntity && homeEntity ? `GAME: ${awayEntity} @ ${homeEntity}` : ''}
${subjectTeam ? `SUBJECT'S SIDE: ${subjectTeam}` : ''}
${opposingEntity ? `OPPONENT: ${opposingEntity}` : ''}

ENTITY DISCIPLINE (universal, applies to every sport):
- SUBJECT is ${prop.player}. Never reference a teammate as if they were the opponent.
- The opposing pitcher, defender, or fighter is provided in the data below — use ONLY that name. Never invent an opposing player name.
- If you are not 100% certain which player is the opponent, OMIT the opponent reference rather than guess.
- Never confuse batter with pitcher, teammate with opposition, or subject with an unrelated player.

EV: ${bestEV.toFixed(1)}% | Books: ${bookCount} | Line range: ${lineRange.toFixed(1)} pts | Best book: ${bestLine?.book}
Grade: ${grade} — ${gradeContext}
Book context: ${bookContext}
${kenpomContext}${playerContext}${modelSignal ? `\nModel signal: ${modelSignal}${modelProb >= 0.56 ? ' — CONFIRMS edge' : modelProb <= 0.50 ? ' — CONFLICTS with edge' : ' — weak signal'}` : ''}${matchupSignals.length > 0 ? `\nPIPELINE MATCHUP FLAG: ${matchupSignals.join(' + ')} — this prop was identified by the model BEFORE checking odds. ${matchupConviction >= 20 ? 'HIGH CONVICTION — explain the specific matchup advantage.' : 'Moderate signal.'}` : ''}
${aGradeInstruction}

Rules (apply to every sport):
- For A grades: lead with the specific stat that explains WHY this line is mispriced. Reference the actual numbers from the data block above — do NOT invent numbers.
- For B grades: lead with the edge, mention the best book to get it at.
- For C grades: be measured — "mild edge, worth tracking"
- For all grades: end with the specific action (e.g. "Over ${bestLine?.line} at ${bestLine?.book}")
- Never say "bet" or "must play"
- 2 sentences maximum
- Sound like a sharp friend texting you, not a robot
- If the only labeled opposing player in the data block conflicts with the subject's team, STOP — omit the opposing reference and lean on EV + book consensus.

Sport-specific guidance (use only when that sport's data is present):
- MLB: reference opposing pitcher ONLY by the name labeled "Opposing pitcher" in the context. Reference K rate, umpire, or park factor when available.
- NBA: reference player recent form or injury context when available.
- UFC: reference the labeled OPPONENT fighter by name, cite the specific matchup stat (striking, grappling, finishing rate).

If no technical data available, lead with the EV and book consensus signal.`;
                })()
              }]
            })
          });
          const aiData = await aiResp.json();
          //console.log('AI Jerry response:', JSON.stringify(aiData));
          Jerry = '🎤 ' + (aiData?.content?.[0]?.text || aQuotes[Math.floor(Math.random()*aQuotes.length)]);
        } catch(e) {
          // Fallback to hardcoded quotes if AI fails
          Jerry = grade==='A' ? aQuotes[Math.floor(Math.random()*aQuotes.length)] :
                  grade==='B' ? bQuotes[Math.floor(Math.random()*bQuotes.length)] :
                  grade==='C' ? cQuotes[Math.floor(Math.random()*cQuotes.length)] :
                  dQuotes[Math.floor(Math.random()*dQuotes.length)];
        }

        return {
          ...prop, bestEV, bestSide, bestLine, bestOver, bestUnder,
          lineRange, bookCount, grade, gradeColor, Jerry,
          bestOverEV, bestUnderEV, modelSignal, modelProb,
          matchupConviction, matchupSignals,
          isPipelinePick: matchupConviction >= 20,
        };
       })(prop));
}
console.log(`[PropJerry ${sport}] GradedRaw: ${gradedRaw.length} (non-null: ${gradedRaw.filter(Boolean).length})`);
// Debug: log why props are being filtered
let filteredReasons = {noEV:0, oddsRange:0, books:0, passed:0, matchupOverride:0};
const graded = gradedRaw.filter(p => {
  if(!p) return false;
  const odds = parseFloat(p.bestLine?.odds);
  if(isNaN(odds)) return true;
  const minBooks = propJerrySport==='NHL' || propJerrySport==='MLB' || propJerrySport==='UFC' ? 1 : 2;

  // Pipeline matchup props — pass on conviction alone, EV not required
  // These are analytically identified plays where the matchup is the edge, not the odds
  // Matchup props pass on conviction but NEVER with negative EV — that's contradictory signals
  if(p.matchupConviction >= 15 && p.bestEV >= -1.0 && odds >= -300 && odds <= 150 && p.bookCount >= minBooks) {
    filteredReasons.matchupOverride++;
    return true;
  }

  // EV scanner props — original flow, requires positive EV
  if(p.bestEV <= 0) { filteredReasons.noEV++; return false; }
  if(odds < -300 || odds > 150) { filteredReasons.oddsRange++; return false; }
  if(p.bookCount < minBooks) { filteredReasons.books++; return false; }
  filteredReasons.passed++;
  return true;
})

        .sort((a,b) => {
          // Sort by: matchup conviction first, then EV
          if(a.matchupConviction >= 15 && b.matchupConviction < 15) return -1;
          if(b.matchupConviction >= 15 && a.matchupConviction < 15) return 1;
          if(a.matchupConviction >= 15 && b.matchupConviction >= 15) return b.matchupConviction - a.matchupConviction;
          return b.bestEV - a.bestEV;
        });

      // Cap matchup props to top 3 per game — don't flood with 9 batters from same game
      const matchupPerGame: Record<string, number> = {};
      const cappedGraded = graded.filter(p => {
        if(p.matchupConviction >= 15) {
          const game = p.gameName || p.game || '';
          matchupPerGame[game] = (matchupPerGame[game] || 0) + 1;
          if(matchupPerGame[game] > 3) return false;
        }
        return true;
      }).slice(0, 30);

      // Replace graded with capped version for display
      graded.length = 0;
      graded.push(...cappedGraded);

      console.log(`[PropJerry ${sport}] Filter results:`, JSON.stringify(filteredReasons));
      if(gradedRaw.filter(Boolean).length > 0 && graded.length === 0) {
        // Log first 3 rejected props for diagnosis
        gradedRaw.filter(Boolean).slice(0,3).forEach(p => {
          console.log(`  Rejected: ${p.player} ${p.market} EV=${p.bestEV?.toFixed(2)} odds=${p.bestLine?.odds} books=${p.bookCount} pipeline=${p.isPipelinePick}`);
        });
      }

      // Auto-save A grades to Supabase prop_grades
const aGrades = graded.filter(p => p.grade === 'A');
if(aGrades.length > 0) {
  try {
    // Dedup — check existing props for today before inserting
    const today = new Date().toISOString().split('T')[0];
    const { data: existing } = await supabase
      .from('prop_grades')
      .select('player, market')
      .eq('sport', sport)
      .gte('created_at', today + 'T00:00:00Z');
    const existingKeys = new Set((existing || []).map(e => `${e.player}_${e.market}`));
    const newGrades = aGrades.filter(p => !existingKeys.has(`${p.player}_${p.marketLabel}`));
    if(newGrades.length > 0) {
      await supabase.from('prop_grades').insert(
        newGrades.map(p => ({
          player: p.player,
          market: p.marketLabel,
          grade: p.grade,
          ev: p.bestEV,
          line: p.bestLine?.line,
          game: p.gameName,
          sport: sport,
          best_side: p.bestSide,
          best_odds: p.bestLine?.odds,
          book: p.bestLine?.book,
          result: 'Pending',
        }))
      );
    }
  } catch(e) {}
}
        // Auto-save graded props to Jerry history
const newEntries = graded.map(prop => ({
  id: `${prop.player}_${prop.market}_${Date.now()}`,
  player: prop.player,
  market: prop.marketLabel,
  game: prop.gameName,
  sport: propJerrySport,
  grade: prop.grade,
  gradeColor: prop.gradeColor,
  bestSide: prop.bestSide,
  bestLine: prop.bestLine?.line,
  bestOdds: prop.bestLine?.odds,
  bestBook: prop.bestLine?.book,
  ev: prop.bestEV,
  date: new Date().toLocaleDateString('en-US',{month:'short',day:'numeric'}),
  result: 'Pending',
}));
setJerryHistory(prev => {
  // Avoid duplicates — same player/market/date
  const existing = prev.filter(h =>
    !newEntries.some(n =>
      n.player===h.player &&
      n.market===h.market &&
      n.date===h.date
    )
  );
  const updated = [...newEntries, ...existing].slice(0,200);
  AsyncStorage.setItem(JERRY_HISTORY_KEY, JSON.stringify(updated));
  return updated;
});

        setPropJerryData(graded);
        try {
          await AsyncStorage.setItem(PROP_JERRY_CACHE_KEY+'_'+sport, JSON.stringify({data:graded, timestamp:Date.now()}));
        } catch(e) {}
        // Save to Supabase cache — shared across all users
        try {
          await supabase.from('prop_jerry_cache').upsert({
            sport: sport,
            data: graded,
            fetched_at: new Date().toISOString(),
          }, {onConflict: 'sport'});
        } catch(e) {}
    } catch(e) {
      //console.log('PropJerry error:', e?.message);
    }
    setPropJerryLastUpdate(new Date());
    setPropJerryLoading(false);
  };

  useEffect(()=>{if(activeTab==='odds')fetchOdds(oddsSport);},[activeTab,oddsSport]);
 useEffect(()=>{
  if(activeTab==='games') {
    fetchGames(gamesSport,gamesDay);
    if(gamesSport==='MLB') { fetchMLBGameContext(); fetchHRWatch(); }
  }
},[activeTab,gamesSport,gamesDay,bartData.length]);
  useEffect(()=>{
    if(activeTab==='stats'){
      if(statsTab==='props')fetchProps(propsSport);
      if(statsTab==='players')fetchPlayerStats();
    }
  },[activeTab,statsTab,propsSport]);
 useEffect(()=>{
    if(activeTab==='trends'){
      if(trendsTab==='propjerry'){fetchPropJerry(propJerrySport);fetchPropOfDay();}
      if(trendsTab==='sharp')fetchSharp(sharpSport);
    }
  },[activeTab,trendsTab,evSport,sharpSport,propJerrySport]);
    useEffect(()=>{
    if(!gameDetailModal||!selectedGame) return;
    setScheduleGamesLoading(true);

    const teamName = scheduleTeam==='away' ? selectedGame.away_team : selectedGame.home_team;

    // NBA — use BDL for real game logs
    if(gamesSport==='NBA') {
      (async()=>{
        try {
          // Find BDL team by matching last word of team name
          const teamLast = teamName.split(' ').pop()?.toLowerCase();
          const teamsResp = await axios.get('https://api.balldontlie.io/v1/teams', {
            headers:{'Authorization':BDL_API_KEY}, params:{per_page:30}
          });
          const bdlTeam = (teamsResp.data?.data||[]).find(t =>
            t.full_name?.toLowerCase().includes(teamLast) || t.name?.toLowerCase()===teamLast
          );
          if(!bdlTeam) { setScheduleGamesLoading(false); return; }

          const gamesResp = await axios.get('https://api.balldontlie.io/v1/games', {
            headers:{'Authorization':BDL_API_KEY},
            params:{'team_ids[]':bdlTeam.id, 'seasons[]':2024, per_page:50}
          });
          const bdlGames = (gamesResp.data?.data||[])
            .filter(g => g.status === 'Final')
            .sort((a,b) => new Date(b.date).getTime() - new Date(a.date).getTime())
            .slice(0,10);

          const mapped = bdlGames.map(g => {
            const isHome = g.home_team?.id === bdlTeam.id;
            const tScore = isHome ? g.home_team_score : g.visitor_team_score;
            const oScore = isHome ? g.visitor_team_score : g.home_team_score;
            const opp = isHome ? g.visitor_team : g.home_team;
            const win = tScore > oScore;
            const d = new Date(g.date);
            return {
              date: (d.getMonth()+1)+'/'+(d.getDate()),
              opp: opp?.name || opp?.full_name?.split(' ').pop() || '?',
              home: isHome,
              score: tScore+'-'+oScore,
              win,
              atsWin: win, // no spread data from BDL
              ouOver: (tScore+oScore) > 220,
              isReal: true,
            };
          });
          setScheduleGames(mapped);
        } catch(e) {
          // Fall back to odds API scores
          const scores = await fetchScores(gamesSport);
          setScheduleGames(getTeamGamesFromScores(scores, teamName, gamesSport));
        }
        setScheduleGamesLoading(false);
      })();
      return;
    }

    // MLB — use MLB Stats API for real game logs
    if(gamesSport==='MLB') {
      (async()=>{
        try {
          // Get MLB team ID
          const teamsResp = await axios.get('https://statsapi.mlb.com/api/v1/teams?sportId=1');
          const teamLast = teamName.split(' ').pop()?.toLowerCase();
          const mlbTeam = (teamsResp.data?.teams||[]).find(t =>
            t.name?.toLowerCase().includes(teamLast) || t.teamName?.toLowerCase()===teamLast
          );
          if(!mlbTeam) { setScheduleGamesLoading(false); return; }
          const today = new Date().toISOString().split('T')[0];
          const thirtyAgo = new Date(Date.now()-30*86400000).toISOString().split('T')[0];
          const schedResp = await axios.get(`https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId=${mlbTeam.id}&startDate=${thirtyAgo}&endDate=${today}&hydrate=linescore`);

          const allGames = (schedResp.data?.dates||[]).flatMap(d => d.games||[])
            .filter(g => g.status?.detailedState === 'Final')
            .reverse()
            .slice(0,10);

          const mapped = allGames.map(g => {
            const isHome = g.teams?.home?.team?.id === mlbTeam.id;
            const tScore = isHome ? g.teams?.home?.score : g.teams?.away?.score;
            const oScore = isHome ? g.teams?.away?.score : g.teams?.home?.score;
            const opp = isHome ? g.teams?.away?.team?.name : g.teams?.home?.team?.name;
            const win = tScore > oScore;
            const d = new Date(g.gameDate);
            return {
              date: (d.getMonth()+1)+'/'+(d.getDate()),
              opp: opp?.split(' ').pop() || '?',
              home: isHome,
              score: tScore+'-'+oScore,
              win,
              atsWin: win,
              ouOver: (tScore+oScore) > 8.5,
              isReal: true,
            };
          });
          setScheduleGames(mapped);
        } catch(e) {
          const scores = await fetchScores(gamesSport);
          setScheduleGames(getTeamGamesFromScores(scores, teamName, gamesSport));
        }
        setScheduleGamesLoading(false);
      })();
      return;
    }

    // All other sports — use odds API scores
    fetchScores(gamesSport).then(scores => {
      if(scheduleTeam==='away') setScheduleGames(getTeamGamesFromScores(scores, selectedGame.away_team, gamesSport));
      else if(scheduleTeam==='home') setScheduleGames(getTeamGamesFromScores(scores, selectedGame.home_team, gamesSport));
      else setScheduleGames(getH2HGamesFromScores(scores, selectedGame.away_team, selectedGame.home_team));
      setScheduleGamesLoading(false);
    });
  },[scheduleTeam, gameDetailModal, selectedGame]);

  const onRefresh=()=>{
    setRefreshing(true);
    if(activeTab==='odds')fetchOdds(oddsSport);
    else if(activeTab==='games') fetchGames(gamesSport,gamesDay,true);
    else if(activeTab==='stats'){if(statsTab==='props')fetchProps(propsSport);else fetchPlayerStats();}
    else if(activeTab==='trends'){if(trendsTab==='sharp')fetchSharp(sharpSport);else setRefreshing(false);}
    else setRefreshing(false);
  };

  const formatSpread=(outcomes)=>outcomes?outcomes.map(o=>o.name.split(' ').pop()+' '+(o.point>0?'+':'')+o.point).join(' / '):'N/A';
  const formatOdds=(outcomes)=>outcomes?outcomes.map(o=>(o.price>0?'+':'')+o.price).join(' / '):'';
  const getBestSpread=(game)=>{
    if(!game.bookmakers||!game.bookmakers.length)return null;
    let best=null,bestLine=Infinity;
    game.bookmakers.forEach(bm=>{
      const s=bm.markets&&bm.markets.find(m=>m.key==='spreads');
      if(s&&s.outcomes&&s.outcomes[0]){const pt=Math.abs(s.outcomes[0].point);if(pt<bestLine){bestLine=pt;best=BOOKMAKER_MAP[bm.key]||bm.key;}}
    });
    return best;
  };
  const getGameSummary=(game)=>{
    const bm=game.bookmakers&&game.bookmakers[0];
    const spread=bm&&bm.markets&&bm.markets.find(m=>m.key==='spreads');
    const total=bm&&bm.markets&&bm.markets.find(m=>m.key==='totals');
    const ml=bm&&bm.markets&&bm.markets.find(m=>m.key==='h2h');
    const awayML=ml&&ml.outcomes&&ml.outcomes.find(o=>o.name===game.away_team);
    const homeML=ml&&ml.outcomes&&ml.outcomes.find(o=>o.name===game.home_team);
    return{
      spread:spread&&spread.outcomes&&spread.outcomes[0]?spread.outcomes[0].name.split(' ').pop()+' '+(spread.outcomes[0].point>0?'+':'')+spread.outcomes[0].point:'N/A',
      total:total&&total.outcomes&&total.outcomes[0]?'O/U '+total.outcomes[0].point:'N/A',
      mlAway:awayML?(awayML.price>0?'+':'')+awayML.price:'N/A',
      mlHome:homeML?(homeML.price>0?'+':'')+homeML.price:'N/A',
    };
  };

  const rankColor=(rank,total=30)=>{
    const pct=rank/total;
    if(pct<=0.33) return '#00e5a0';
    if(pct<=0.66) return '#ffd166';
    return '#ff4d6d';
  };

  const renderMatchupView=(game, sport='NBA')=>{
    if(!game) return null;
    const md=getMatchupData(game);
    if(!md) return null;
    const bookmakers=game.bookmakers||[];
    //console.log('Bookmakers:', bookmakers.map(b=>b.key));
    const awayShort=md.away.split(' ').pop();
    const homeShort=md.home.split(' ').pop();

    return(
      <View style={{marginBottom:16}}>
        {/* Tab bar */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:12}}>
          <View style={{flexDirection:'row',gap:6}}>
            {[{id:'money',label:'📚 BOOK CONSENSUS'},{id:'schedule',label:'📅 Schedule'},{id:'stats',label:'📊 Team Stats'},{id:'situational',label:'📋 Situational'}].map(t=>(
              <TouchableOpacity key={t.id} style={[styles.chipBtn,matchupTab===t.id&&{backgroundColor:'rgba(255,184,0,0.12)',borderColor:HRB_COLOR}]} onPress={()=>setMatchupTab(t.id)}>
                <Text style={[styles.chipTxt,matchupTab===t.id&&{color:HRB_COLOR,fontWeight:'700'}]}>{t.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </ScrollView>

           {/* Jerry Game Narrative */}
          <View style={{marginHorizontal:16,marginBottom:12,backgroundColor:'rgba(255,184,0,0.06)',borderRadius:14,padding:14,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
            <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:12,marginBottom:8}}>🎤 JERRY'S READ</Text>
            {gameNarrativeLoading?(
              <View style={{flexDirection:'row',alignItems:'center',gap:8}}>
                <ActivityIndicator size='small' color={HRB_COLOR}/>
                <Text style={{color:'#4a6070',fontSize:13}}>Jerry is breaking down the tape...</Text>
              </View>
            ):(
              <Text style={{color:'#c8d8e8',fontSize:13,lineHeight:20,fontStyle:'italic'}}>"{gameNarrative?.replace(/\*\*(.*?)\*\*/g, '$1').replace(/\*(.*?)\*/g, '$1').replace(/#{1,6}\s/g, '').trim()}"</Text>
            )}
          </View>

           {matchupTab==='money'&&(
                  <View style={{padding:16}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:14,marginBottom:4}}>📚 BOOK CONSENSUS</Text>
                    <Text style={{color:'#4a6070',fontSize:11,marginBottom:16}}>Real data from {bookmakers?.length||0} sportsbooks</Text>
                    {(()=>{
                      const spreads = bookmakers.map(bm=>{
                        const s=bm.markets&&bm.markets.find(m=>m.key==='spreads');
                        return s&&s.outcomes&&s.outcomes[0]?{book:BOOKMAKER_MAP[bm.key]||bm.key,line:s.outcomes[0].point,isHRB:bm.key==='hardrockbet' ||bm.key==='hardrock'}:null;
                      }).filter(Boolean);
                      const totals = bookmakers.map(bm=>{
                        const s=bm.markets&&bm.markets.find(m=>m.key==='totals');
                        return s&&s.outcomes&&s.outcomes[0]?{book:BOOKMAKER_MAP[bm.key]||bm.key,line:s.outcomes[0].point,isHRB:bm.key==='hardrockbet' ||bm.key==='hardrock'}:null;
                      }).filter(Boolean);
                      const mls = bookmakers.map(bm=>{
                        const s=bm.markets&&bm.markets.find(m=>m.key==='h2h');
                        return s&&s.outcomes&&s.outcomes[0]?{book:BOOKMAKER_MAP[bm.key]||bm.key,line:s.outcomes[0].price,isHRB:bm.key==='hardrockbet' ||bm.key==='hardrock'}:null;
                      }).filter(Boolean);
                      const spreadLines = spreads.map(s=>s.line);
                      const totalLines = totals.map(t=>t.line);
                      const mlLines = mls.map(m=>m.line);
                      const spreadRange = spreadLines.length>1?Math.max(...spreadLines)-Math.min(...spreadLines):0;
                      const totalRange = totalLines.length>1?Math.max(...totalLines)-Math.min(...totalLines):0;
                      const mlRange = mlLines.length>1?Math.max(...mlLines)-Math.min(...mlLines):0;
                      const isLocked = spreadRange<=0.5&&totalRange<=0.5;
                      return(
                        <View>
                          <View style={{backgroundColor:'#151c24',borderRadius:12,padding:14,marginBottom:12,alignItems:'center'}}>
                            <Text style={{color:isLocked?'#00e5a0':'#FFB800',fontSize:22,fontWeight:'800'}}>{isLocked?'🔒 Locked':'⚡ Variance'}</Text>
                            <Text style={{color:'#7a92a8',fontSize:11,marginTop:4}}>{isLocked?'Books in tight agreement':'Books disagree — potential sharp opportunity'}</Text>
                          </View>
                          {/* Spread */}
                          <View style={{backgroundColor:'#151c24',borderRadius:12,padding:14,marginBottom:8}}>
                            <View style={{flexDirection:'row',justifyContent:'space-between',marginBottom:8}}>
                              <Text style={{color:'#e8f0f8',fontWeight:'700'}}>SPREAD</Text>
                              <Text style={{color:spreadRange<=0.5?'#00e5a0':'#FFB800',fontWeight:'700'}}>{spreadRange===0?'Consensus':spreadRange.toFixed(1)+' pt range'}</Text>
                            </View>
                            {spreads.map((s,i)=>(
                              <View key={i} style={{flexDirection:'row',justifyContent:'space-between',paddingVertical:4,borderTopWidth:i>0?1:0,borderTopColor:'#1f2d3d'}}>
                                <Text style={{color:s.isHRB?HRB_COLOR:'#7a92a8',fontSize:12,fontWeight:s.isHRB?'700':'400'}}>{s.isHRB?'🎸 ':''}{s.book}</Text>
                                <Text style={{color:s.isHRB?HRB_COLOR:'#e8f0f8',fontSize:12,fontWeight:s.isHRB?'700':'400'}}>{s.line>0?'+':''}{s.line}</Text>
                              </View>
                            ))}
                          </View>
                          {/* Total */}
                          <View style={{backgroundColor:'#151c24',borderRadius:12,padding:14,marginBottom:8}}>
                            <View style={{flexDirection:'row',justifyContent:'space-between',marginBottom:8}}>
                              <Text style={{color:'#e8f0f8',fontWeight:'700'}}>TOTAL (O/U)</Text>
                              <Text style={{color:totalRange<=0.5?'#00e5a0':'#FFB800',fontWeight:'700'}}>{totalRange===0?'Consensus':totalRange.toFixed(1)+' pt range'}</Text>
                            </View>
                            {totals.map((t,i)=>(
                              <View key={i} style={{flexDirection:'row',justifyContent:'space-between',paddingVertical:4,borderTopWidth:i>0?1:0,borderTopColor:'#1f2d3d'}}>
                                <Text style={{color:t.isHRB?HRB_COLOR:'#7a92a8',fontSize:12,fontWeight:t.isHRB?'700':'400'}}>{t.isHRB?'🎸 ':''}{t.book}</Text>
                                <Text style={{color:t.isHRB?HRB_COLOR:'#e8f0f8',fontSize:12,fontWeight:t.isHRB?'700':'400'}}>{t.line}</Text>
                              </View>
                            ))}
                          </View>
                          {/* ML */}
                          <View style={{backgroundColor:'#151c24',borderRadius:12,padding:14}}>
                            <View style={{flexDirection:'row',justifyContent:'space-between',marginBottom:8}}>
                              <Text style={{color:'#e8f0f8',fontWeight:'700'}}>MONEYLINE</Text>
                              <Text style={{color:mlRange<=10?'#00e5a0':'#FFB800',fontWeight:'700'}}>{mlRange<=10?'Consensus':'+'+Math.round(mlRange)+' range'}</Text>
                            </View>
                            {mls.map((m,i)=>(
                              <View key={i} style={{flexDirection:'row',justifyContent:'space-between',paddingVertical:4,borderTopWidth:i>0?1:0,borderTopColor:'#1f2d3d'}}>
                                <Text style={{color:m.isHRB?HRB_COLOR:'#7a92a8',fontSize:12,fontWeight:m.isHRB?'700':'400'}}>{m.isHRB?'🎸 ':''}{m.book}</Text>
                                <Text style={{color:m.isHRB?HRB_COLOR:'#e8f0f8',fontSize:12,fontWeight:m.isHRB?'700':'400'}}>{m.line>0?'+':''}{m.line}</Text>
                              </View>
                            ))}
                          </View>
                        </View>
                      );
                    })()}
                  </View>
                )}


            {/* SCHEDULE TAB */}
        {matchupTab==='schedule'&&(()=>{
          if(gamesSport==='NHL') return(
            <View style={{backgroundColor:'#0a1018',borderRadius:14,padding:20,borderWidth:1,borderColor:'#1f2d3d',alignItems:'center'}}>
              <Text style={{fontSize:32}}>🏒</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:16,marginTop:12}}>NHL Schedule Data</Text>
              <Text style={{color:'#7a92a8',fontSize:13,marginTop:8,textAlign:'center',lineHeight:20}}>Real-time schedule and game log data will be available for the 2026-27 NHL season.</Text>
              <View style={{marginTop:12,backgroundColor:'rgba(255,184,0,0.1)',borderRadius:10,paddingHorizontal:14,paddingVertical:8,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
                <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:11}}>🔜 COMING NEXT SEASON</Text>
              </View>
            </View>
          );
          const games = scheduleGames.length > 0 ? scheduleGames : (scheduleTeam==='away'?md.awayGames:scheduleTeam==='home'?md.homeGames:md.h2hGames);
          const isReal = scheduleGames.length > 0;
          return(
            <View style={{backgroundColor:'#0a1018',borderRadius:14,padding:14,borderWidth:1,borderColor:'#1f2d3d'}}>
              <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
                <View style={{flexDirection:'row',gap:6}}>
                  {[{id:'away',label:awayShort},{id:'home',label:homeShort}].map(t=>(
                    <TouchableOpacity key={t.id} style={[{flex:1,alignItems:'center',paddingVertical:8,paddingHorizontal:12,borderRadius:10,borderWidth:1,borderColor:'#1f2d3d',backgroundColor:'#151c24'},scheduleTeam===t.id&&{backgroundColor:'rgba(255,184,0,0.12)',borderColor:HRB_COLOR}]} onPress={()=>setScheduleTeam(t.id)}>
                      <Text style={{color:scheduleTeam===t.id?HRB_COLOR:'#7a92a8',fontWeight:'700',fontSize:12}}>{t.label}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
              {scheduleGamesLoading?(
                <View style={{alignItems:'center',paddingVertical:20}}><ActivityIndicator size="small" color={HRB_COLOR}/><Text style={{color:'#7a92a8',fontSize:11,marginTop:8}}>Loading results...</Text></View>
              ):(
                <>
                  <View style={{flexDirection:'row',marginBottom:8,paddingHorizontal:4}}>
                    <Text style={{flex:1,color:'#4a6070',fontSize:10,fontWeight:'700'}}>DATE</Text>
                    <Text style={{flex:2,color:'#4a6070',fontSize:10,fontWeight:'700'}}>OPP</Text>
                    <Text style={{flex:1.5,color:'#4a6070',fontSize:10,fontWeight:'700',textAlign:'center'}}>RESULT</Text>
                    <Text style={{flex:1,color:'#4a6070',fontSize:10,fontWeight:'700',textAlign:'center'}}>ATS</Text>
                    <Text style={{flex:1,color:'#4a6070',fontSize:10,fontWeight:'700',textAlign:'center'}}>O/U</Text>
                  </View>
                  {games.map((g,i)=>(
                    <View key={i} style={{flexDirection:'row',alignItems:'center',paddingVertical:8,borderTopWidth:1,borderTopColor:'#1f2d3d',paddingHorizontal:4}}>
                      <Text style={{flex:1,color:'#7a92a8',fontSize:11}}>{g.date}</Text>
                      <Text style={{flex:2,color:'#e8f0f8',fontSize:11,fontWeight:'600'}}>{g.home?'vs':'@'} {g.opp}</Text>
                      <View style={{flex:1.5,alignItems:'center',flexDirection:'row',justifyContent:'center',gap:4}}>
                        <View style={{width:20,height:20,borderRadius:10,backgroundColor:g.win?'rgba(0,229,160,0.2)':'rgba(255,77,109,0.2)',alignItems:'center',justifyContent:'center',borderWidth:1,borderColor:g.win?'#00e5a0':'#ff4d6d'}}>
                          <Text style={{color:g.win?'#00e5a0':'#ff4d6d',fontSize:10,fontWeight:'800'}}>{g.win?'W':'L'}</Text>
                        </View>
                        <Text style={{color:'#7a92a8',fontSize:10}}>{g.score}</Text>
                      </View>
                      <View style={{flex:1,alignItems:'center'}}>
                        <View style={{width:20,height:20,borderRadius:10,backgroundColor:g.atsWin?'rgba(0,229,160,0.2)':'rgba(255,77,109,0.2)',alignItems:'center',justifyContent:'center',borderWidth:1,borderColor:g.atsWin?'#00e5a0':'#ff4d6d'}}>
                          <Text style={{color:g.atsWin?'#00e5a0':'#ff4d6d',fontSize:10,fontWeight:'800'}}>{g.atsWin?'W':'L'}</Text>
                        </View>
                      </View>
                      <View style={{flex:1,alignItems:'center'}}>
                        <View style={{width:20,height:20,borderRadius:10,backgroundColor:g.ouOver?'rgba(0,153,255,0.2)':'rgba(255,209,102,0.2)',alignItems:'center',justifyContent:'center',borderWidth:1,borderColor:g.ouOver?'#0099ff':'#ffd166'}}>
                          <Text style={{color:g.ouOver?'#0099ff':'#ffd166',fontSize:10,fontWeight:'800'}}>{g.ouOver?'O':'U'}</Text>
                        </View>
                      </View>
                    </View>
                  ))}
                  {games.length===0&&<View style={{alignItems:'center',paddingVertical:20}}><Text style={{color:'#7a92a8',fontSize:12}}>No recent results found.</Text></View>}
                  {!isReal&&<Text style={{color:'#4a6070',fontSize:10,textAlign:'right',marginTop:8}}>* Simulated data</Text>}
                </>
              )}
            </View>
          );
        })()}

            {/* TEAM STATS TAB */}
        {matchupTab==='stats'&&(()=>{
          if(gamesSport==='NHL') return(
            <View style={{backgroundColor:'#0a1018',borderRadius:14,padding:20,borderWidth:1,borderColor:'#1f2d3d',alignItems:'center'}}>
              <Text style={{fontSize:32}}>🏒</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:16,marginTop:12}}>NHL Team Stats</Text>
              <Text style={{color:'#7a92a8',fontSize:13,marginTop:8,textAlign:'center',lineHeight:20}}>Advanced team stats and efficiency data will be available for the 2026-27 NHL season.</Text>
              <View style={{marginTop:12,backgroundColor:'rgba(255,184,0,0.1)',borderRadius:10,paddingHorizontal:14,paddingVertical:8,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
                <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:11}}>🔜 COMING NEXT SEASON</Text>
              </View>
            </View>
          );
          const isNCAAB = gamesSport==='NCAAB';
          const isNBA = gamesSport==='NBA';
          const isMLB = gamesSport==='MLB';
          const nbaTeamList = isNBA ? Object.values(nbaTeamData) : [];
          const awayReal = isNCAAB ? fuzzyMatchTeam(md.away, bartData, 'team') : isNBA ? fuzzyMatchTeam(md.away, nbaTeamList, 'team') : null;
          const homeReal = isNCAAB ? fuzzyMatchTeam(md.home, bartData, 'team') : isNBA ? fuzzyMatchTeam(md.home, nbaTeamList, 'team') : null;
          const mlbCtx = isMLB ? (mlbGameContext[selectedGame?.home_team] ||
            mlbGameContext[selectedGame?.away_team] ||
            mlbGameContext[selectedGame?.home_team?.trim()] ||
            mlbGameContext[selectedGame?.away_team?.trim()] ||
            Object.values(mlbGameContext).find((ctx: any) =>
              ctx.home_team === selectedGame?.home_team ||
              ctx.away_team === selectedGame?.away_team ||
              ctx.home_team === selectedGame?.away_team ||
              ctx.away_team === selectedGame?.home_team
            )) : null;
          const hasReal = (awayReal && homeReal) || mlbCtx;
          const statCats = isNBA && awayReal && homeReal ? [
            {label:'Net Rating', away: awayReal.net_rating?.toFixed(1), home: homeReal.net_rating?.toFixed(1), higherBetter: true},
            {label:'Off Rating', away: awayReal.offensive_rating?.toFixed(1), home: homeReal.offensive_rating?.toFixed(1), higherBetter: true},
            {label:'Def Rating', away: awayReal.defensive_rating?.toFixed(1), home: homeReal.defensive_rating?.toFixed(1), higherBetter: false},
            {label:'eFG%', away: awayReal.efg_pct?.toFixed(1)+'%', home: homeReal.efg_pct?.toFixed(1)+'%', higherBetter: true},
            {label:'Pace', away: awayReal.pace?.toFixed(1), home: homeReal.pace?.toFixed(1), higherBetter: null},
          ] : (awayReal && homeReal) ? [
            {label:'Off Efficiency', away: awayReal.adjOERank, home: homeReal.adjOERank, higherBetter: null},
            {label:'Def Efficiency', away: awayReal.adjDERank, home: homeReal.adjDERank, higherBetter: null},
            {label:'Tempo', away: awayReal.tempoRank, home: homeReal.tempoRank, higherBetter: null},
          ] : md.statCategories;
          const totalTeams = isNCAAB ? bartData.length||358 : 30;
          return(
            <View style={{backgroundColor:'#0a1018',borderRadius:14,padding:14,borderWidth:1,borderColor:'#1f2d3d'}}>
              {hasReal&&<View style={{flexDirection:'row',justifyContent:'flex-end',marginBottom:10}}>
                <View style={{backgroundColor:'rgba(0,229,160,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,borderWidth:1,borderColor:'rgba(0,229,160,0.3)'}}><Text style={{color:'#00e5a0',fontSize:9,fontWeight:'800'}}>📡 LIVE DATA</Text></View>
              </View>}
              <View style={{flexDirection:'row',justifyContent:'space-between',marginBottom:10}}>
                <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:12}}>{awayShort}</Text>
                <Text style={{color:'#4a6070',fontSize:11,fontWeight:'600'}}>STAT CATEGORY</Text>
                <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:12}}>{homeShort}</Text>
              </View>
              {!isMLB && (hasReal ? statCats : md.statCategories).map((stat,i)=>{
               const awayVal = stat.away || 0;
               const homeVal = stat.home || 0;
               const hb = stat.higherBetter;
               // For NBA/value-based stats, use higherBetter coloring
               const useComparison = (isNBA || isMLB) && hb !== undefined;
               const awayNum = parseFloat(awayVal);
               const homeNum = parseFloat(homeVal);
               const awayBetter = useComparison ? (hb===true ? awayNum>homeNum : hb===false ? awayNum<homeNum : null) : null;
               const homeBetter = useComparison ? (hb===true ? homeNum>awayNum : hb===false ? homeNum<awayNum : null) : null;
               const awayColor = useComparison ? (awayBetter?'#00e5a0':homeBetter?'#ff4d6d':'#7a92a8') : rankColor(awayVal, totalTeams);
               const homeColor = useComparison ? (homeBetter?'#00e5a0':awayBetter?'#ff4d6d':'#7a92a8') : rankColor(homeVal, totalTeams);
                return(
                  <View key={i} style={{flexDirection:'row',alignItems:'center',marginBottom:10}}>
                    <View style={{flex:1,alignItems:'flex-start'}}>
                      <View style={{paddingHorizontal:10,paddingVertical:5,borderRadius:8,backgroundColor:awayColor+'22',borderWidth:1,borderColor:awayColor+'44',minWidth:48,alignItems:'center'}}>
                        <Text style={{color:awayColor,fontWeight:awayBetter?'800':'700',fontSize:12}}>{isNCAAB?'#':''}{awayVal}</Text>
                      </View>
                    </View>
                    <Text style={{flex:1.5,color:'#7a92a8',fontSize:11,textAlign:'center'}}>{stat.label}</Text>
                    <View style={{flex:1,alignItems:'flex-end'}}>
                      <View style={{paddingHorizontal:10,paddingVertical:5,borderRadius:8,backgroundColor:homeColor+'22',borderWidth:1,borderColor:homeColor+'44',minWidth:48,alignItems:'center'}}>
                        <Text style={{color:homeColor,fontWeight:homeBetter?'800':'700',fontSize:12}}>{isNCAAB?'#':''}{homeVal}</Text>
                      </View>
                    </View>
                  </View>
                );
              })}
              {isMLB&&mlbCtx&&(()=>{
                const pitcherCtxStr = mlbCtx.pitcher_context || '';
                const homeCtx = pitcherCtxStr.split('|')[0] || '';
                const awayCtx = pitcherCtxStr.split('|')[1] || '';
                const homeKRate = homeCtx.match(/K% ([\d.]+)/)?.[1] || 'N/A';
                const awayKRate = awayCtx.match(/K% ([\d.]+)/)?.[1] || 'N/A';
                const mlbRows = [
                  {label:'SP xERA', away: mlbCtx.away_sp_xera && parseFloat(mlbCtx.away_sp_xera) <= 6.5 ? parseFloat(mlbCtx.away_sp_xera).toFixed(2) : 'N/A', home: mlbCtx.home_sp_xera && parseFloat(mlbCtx.home_sp_xera) <= 6.5 ? parseFloat(mlbCtx.home_sp_xera).toFixed(2) : 'N/A', higherBetter: false},
                  {label:'SP K%', away: awayKRate, home: homeKRate, higherBetter: true},
                  {label:'wRC+', away: mlbCtx.away_wrc_plus?.toFixed(0) || 'N/A', home: mlbCtx.home_wrc_plus?.toFixed(0) || 'N/A', higherBetter: true},
                  {label:'wOBA', away: mlbCtx.away_woba?.toFixed(3) || 'N/A', home: mlbCtx.home_woba?.toFixed(3) || 'N/A', higherBetter: true},
                  {label:'R/G', away: mlbCtx.away_runs_per_game?.toFixed(2) || 'N/A', home: mlbCtx.home_runs_per_game?.toFixed(2) || 'N/A', higherBetter: true},
                ];
                return(
                  <View>
                    {/* Pitcher matchup header */}
                    <View style={{backgroundColor:'rgba(255,184,0,0.07)',borderRadius:10,padding:10,marginBottom:10,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
                      <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:11,marginBottom:6}}>PITCHER MATCHUP</Text>
                      <View style={{flexDirection:'row',justifyContent:'space-between'}}>
                        <View style={{flex:1}}>
                          <Text style={{color:'#e8f0f8',fontSize:12,fontWeight:'700'}}>{mlbCtx.away_pitcher || selectedGame?.away_pitcher || 'TBD'}</Text>
                          <Text style={{color:'#7a92a8',fontSize:10}}>xERA: {mlbCtx.away_sp_xera && parseFloat(mlbCtx.away_sp_xera) <= 6.5 ? parseFloat(mlbCtx.away_sp_xera).toFixed(2) : 'N/A'}</Text>
                        </View>
                        <Text style={{color:'#4a6070',fontSize:11,fontWeight:'700',alignSelf:'center'}}>vs</Text>
                        <View style={{flex:1,alignItems:'flex-end'}}>
                          <Text style={{color:'#e8f0f8',fontSize:12,fontWeight:'700'}}>{mlbCtx.home_pitcher || selectedGame?.home_pitcher || 'TBD'}</Text>
                          <Text style={{color:'#7a92a8',fontSize:10}}>xERA: {mlbCtx.home_sp_xera && parseFloat(mlbCtx.home_sp_xera) <= 6.5 ? parseFloat(mlbCtx.home_sp_xera).toFixed(2) : 'N/A'}</Text>
                        </View>
                      </View>
                    </View>
                    {/* Stat comparison table */}
                    {mlbRows.map((row,i)=>{
                      const awayNum = parseFloat(row.away);
                      const homeNum = parseFloat(row.home);
                      const awayBetter = row.higherBetter===true ? awayNum>homeNum : row.higherBetter===false ? awayNum<homeNum : null;
                      const homeBetter = row.higherBetter===true ? homeNum>awayNum : row.higherBetter===false ? homeNum<awayNum : null;
                      return(
                        <View key={i} style={{flexDirection:'row',alignItems:'center',marginBottom:10}}>
                          <View style={{flex:1,alignItems:'flex-start'}}>
                            <View style={{paddingHorizontal:10,paddingVertical:5,borderRadius:8,backgroundColor:(awayBetter?'#00e5a0':homeBetter?'#ff4d6d':'#7a92a8')+'22',borderWidth:1,borderColor:(awayBetter?'#00e5a0':homeBetter?'#ff4d6d':'#7a92a8')+'44',minWidth:48,alignItems:'center'}}>
                              <Text style={{color:awayBetter?'#00e5a0':homeBetter?'#ff4d6d':'#7a92a8',fontWeight:awayBetter?'800':'700',fontSize:12}}>{row.away}</Text>
                            </View>
                          </View>
                          <Text style={{flex:1.5,color:'#7a92a8',fontSize:11,textAlign:'center'}}>{row.label}</Text>
                          <View style={{flex:1,alignItems:'flex-end'}}>
                            <View style={{paddingHorizontal:10,paddingVertical:5,borderRadius:8,backgroundColor:(homeBetter?'#00e5a0':awayBetter?'#ff4d6d':'#7a92a8')+'22',borderWidth:1,borderColor:(homeBetter?'#00e5a0':awayBetter?'#ff4d6d':'#7a92a8')+'44',minWidth:48,alignItems:'center'}}>
                              <Text style={{color:homeBetter?'#00e5a0':awayBetter?'#ff4d6d':'#7a92a8',fontWeight:homeBetter?'800':'700',fontSize:12}}>{row.home}</Text>
                            </View>
                          </View>
                        </View>
                      );
                    })}
                    {/* Park & weather */}
                    <View style={{backgroundColor:'#151c24',borderRadius:10,padding:10,gap:4}}>
                      <View style={{flexDirection:'row',justifyContent:'space-between'}}>
                        <Text style={{color:'#7a92a8',fontSize:11}}>Park Factor</Text>
                        <Text style={{color:mlbCtx.park_run_factor>=110?'#ff4d6d':mlbCtx.park_run_factor<=93?'#00e5a0':'#e8f0f8',fontWeight:'700',fontSize:12}}>{mlbCtx.park_run_factor || 'N/A'}</Text>
                      </View>
                      <View style={{flexDirection:'row',justifyContent:'space-between'}}>
                        <Text style={{color:'#7a92a8',fontSize:11}}>Weather</Text>
                        <Text style={{color:'#e8f0f8',fontSize:12}}>{mlbCtx.temperature ? mlbCtx.temperature+'°F' : 'N/A'} | {mlbCtx.wind_speed ? mlbCtx.wind_speed+'mph '+mlbCtx.wind_direction : 'N/A'}</Text>
                      </View>
                    </View>
                  </View>
                );
              })()}
              {hasReal&&awayReal&&homeReal&&(
                <View style={{marginTop:8,backgroundColor:'rgba(255,184,0,0.07)',borderRadius:10,padding:10,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
                  {isNCAAB&&<Text style={{color:'#e8f0f8',fontSize:12,lineHeight:18}}>
                    <Text style={{color:HRB_COLOR,fontWeight:'700'}}>{awayShort}</Text> AdjOE: <Text style={{color:'#00e5a0',fontWeight:'700'}}>{awayReal.adjOE.toFixed(1)}</Text> | AdjDE: <Text style={{color:'#0099ff',fontWeight:'700'}}>{awayReal.adjDE.toFixed(1)}</Text>{'\n'}
                    <Text style={{color:HRB_COLOR,fontWeight:'700'}}>{homeShort}</Text> AdjOE: <Text style={{color:'#00e5a0',fontWeight:'700'}}>{homeReal.adjOE.toFixed(1)}</Text> | AdjDE: <Text style={{color:'#0099ff',fontWeight:'700'}}>{homeReal.adjDE.toFixed(1)}</Text>
                  </Text>}
                  <Text style={{color:'#4a6070',fontSize:10,marginTop:6}}>📡 {isNCAAB?'Live Efficiency':'BDL'} live data</Text>
                   {isNCAAB&&awayReal&&homeReal&&(
                    <View style={{marginTop:10,gap:6}}>
                      <View style={{flexDirection:'row',justifyContent:'space-between',paddingVertical:6,borderTopWidth:1,borderTopColor:'#1f2d3d'}}>
                        <Text style={{color:'#4a6070',fontSize:10,fontWeight:'700'}}>STAT</Text>
                        <Text style={{color:'#4a6070',fontSize:10,fontWeight:'700'}}>{awayShort}</Text>
                        <Text style={{color:'#4a6070',fontSize:10,fontWeight:'700'}}>{homeShort}</Text>
                      </View>
                      {[
                        {label:'AdjEM',away:(awayReal.adjEM||0).toFixed(1),home:(homeReal.adjEM||0).toFixed(1),higherBetter:true},
                        {label:'AdjOE',away:(awayReal.adjOE||0).toFixed(1),home:(homeReal.adjOE||0).toFixed(1),higherBetter:true},
                        {label:'AdjDE',away:(awayReal.adjDE||0).toFixed(1),home:(homeReal.adjDE||0).toFixed(1),higherBetter:false},
                        {label:'Tempo',away:(awayReal.tempo||0).toFixed(1),home:(homeReal.tempo||0).toFixed(1),higherBetter:null},
                        {label:'Record',away:(awayReal.wins||0)+'-'+(awayReal.losses||0),home:(homeReal.wins||0)+'-'+(homeReal.losses||0),higherBetter:null},
                        {label:'Conference',away:awayReal.conf||'N/A',home:homeReal.conf||'N/A',higherBetter:null},
                        {label:'Seed',away:awayReal.seed?String(awayReal.seed):'—',home:homeReal.seed?String(homeReal.seed):'—',higherBetter:null},
                      ].map((row,i)=>{
                        const awayVal = parseFloat(row.away);
                        const homeVal = parseFloat(row.home);
                        const awayBetter = row.higherBetter===true ? awayVal>homeVal : row.higherBetter===false ? awayVal<homeVal : null;
                        const homeBetter = row.higherBetter===true ? homeVal>awayVal : row.higherBetter===false ? homeVal<awayVal : null;
                        return(
                          <View key={i} style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',paddingVertical:5,borderTopWidth:1,borderTopColor:'#1f2d3d'}}>
                            <Text style={{color:'#7a92a8',fontSize:11,flex:1}}>{row.label}</Text>
                            <Text style={{color:awayBetter?'#00e5a0':homeBetter?'#ff4d6d':'#e8f0f8',fontWeight:awayBetter?'800':'400',fontSize:12,flex:1,textAlign:'center'}}>{row.away}</Text>
                            <Text style={{color:homeBetter?'#00e5a0':awayBetter?'#ff4d6d':'#e8f0f8',fontWeight:homeBetter?'800':'400',fontSize:12,flex:1,textAlign:'right'}}>{row.home}</Text>
                          </View>
                        );
                      })}
                    </View>
                  )}

                </View>
              )}
              {!hasReal&&!isMLB&&!isNBA&&<Text style={{color:'#4a6070',fontSize:10,textAlign:'right',marginTop:4}}>* Simulated data</Text>}
              <View style={{flexDirection:'row',gap:12,paddingTop:10,borderTopWidth:1,borderTopColor:'#1f2d3d',marginTop:8}}>
                <View style={{flexDirection:'row',alignItems:'center',gap:4}}><View style={{width:8,height:8,borderRadius:4,backgroundColor:'#00e5a0'}}/><Text style={{color:'#7a92a8',fontSize:10}}>Top tier</Text></View>
                <View style={{flexDirection:'row',alignItems:'center',gap:4}}><View style={{width:8,height:8,borderRadius:4,backgroundColor:'#ffd166'}}/><Text style={{color:'#7a92a8',fontSize:10}}>Mid</Text></View>
                <View style={{flexDirection:'row',alignItems:'center',gap:4}}><View style={{width:8,height:8,borderRadius:4,backgroundColor:'#ff4d6d'}}/><Text style={{color:'#7a92a8',fontSize:10}}>Bottom</Text></View>
              </View>
            </View>
          );
        })()}

        {/* SITUATIONAL TAB */}
        {matchupTab==='situational'&&(
          <View style={{backgroundColor:'#0a1018',borderRadius:14,padding:14,borderWidth:1,borderColor:'#1f2d3d'}}>
           {(()=>{
              if(gamesSport==='NCAAB' && bartData.length) {
                const awayT = fuzzyMatchTeam(stripMascot(selectedGame.away_team), bartData, 'team');
                const homeT = fuzzyMatchTeam(stripMascot(selectedGame.home_team), bartData, 'team');
                if(awayT && homeT) {
                  const adjEMGap = Math.abs((homeT.adjEM||0) - (awayT.adjEM||0)).toFixed(1);
                  const sosGap = Math.abs((awayT.sos||0) - (homeT.sos||0)).toFixed(1);
                  const rows = [
                    {label:'Net Efficiency (AdjEM)', away:(awayT.adjEM||0).toFixed(1), home:(homeT.adjEM||0).toFixed(1), awayGood:awayT.adjEM>homeT.adjEM, desc:'Higher = better team'},
                    {label:'Offensive Efficiency', away:(awayT.adjOE||0).toFixed(1), home:(homeT.adjOE||0).toFixed(1), awayGood:awayT.adjOE>homeT.adjOE, desc:'Points per 100 possessions'},
                    {label:'Defensive Efficiency', away:(awayT.adjDE||0).toFixed(1), home:(homeT.adjDE||0).toFixed(1), awayGood:awayT.adjDE<homeT.adjDE, desc:'Lower = better defense'},
                    {label:'Strength of Schedule', away:(awayT.sos||0).toFixed(1), home:(homeT.sos||0).toFixed(1), awayGood:awayT.sos>homeT.sos, desc:'Higher = tougher schedule'},
                    {label:'Luck Factor', away:(awayT.luck||0).toFixed(3), home:(homeT.luck||0).toFixed(3), awayGood:awayT.luck<homeT.luck, desc:'Negative = due for regression'},
                    {label:'Tempo', away:(awayT.tempo||0).toFixed(1), home:(homeT.tempo||0).toFixed(1), awayGood:false, desc:'Possessions per 40 min'},
                  ];
                  return(
                    <View>
                      <View style={{backgroundColor:'rgba(255,184,0,0.08)',borderRadius:10,padding:10,marginBottom:14,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
                        <Text style={{color:HRB_COLOR,fontSize:11,fontWeight:'700',textAlign:'center'}}>📊 HCA: 3.5 pts • AdjEM Gap: {adjEMGap} • SOS Gap: {sosGap}</Text>
                      </View>
                      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
                        <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:13}}>{awayShort}</Text>
                        <Text style={{color:'#4a6070',fontSize:10,fontWeight:'600'}}>LIVE DATA</Text>
                        <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:13}}>{homeShort}</Text>
                      </View>
                      {rows.map((row,i)=>(
                        <View key={i} style={{marginBottom:12}}>
                          <Text style={{color:'#4a6070',fontSize:10,fontWeight:'600',marginBottom:2,textAlign:'center'}}>{row.label}</Text>
                          <Text style={{color:'#4a6071',fontSize:9,textAlign:'center',marginBottom:6}}>{row.desc}</Text>
                          <View style={{flexDirection:'row',gap:8}}>
                            <View style={{flex:1,backgroundColor:row.awayGood?'rgba(0,229,160,0.1)':'rgba(255,77,109,0.1)',borderRadius:10,padding:10,alignItems:'center',borderWidth:1,borderColor:row.awayGood?'rgba(0,229,160,0.3)':'rgba(255,77,109,0.3)'}}>
                              <Text style={{color:row.awayGood?'#00e5a0':'#ff4d6d',fontWeight:'800',fontSize:16}}>{row.away}</Text>
                            </View>
                            <View style={{flex:1,backgroundColor:!row.awayGood?'rgba(0,229,160,0.1)':'rgba(255,77,109,0.1)',borderRadius:10,padding:10,alignItems:'center',borderWidth:1,borderColor:!row.awayGood?'rgba(0,229,160,0.3)':'rgba(255,77,109,0.3)'}}>
                              <Text style={{color:!row.awayGood?'#00e5a0':'#ff4d6d',fontWeight:'800',fontSize:16}}>{row.home}</Text>
                            </View>
                          </View>
                        </View>
                      ))}
                    </View>
                  );
                }
              }
              if(gamesSport==='NBA') {
                const nbaList = Object.values(nbaTeamData);
                const awayT = fuzzyMatchTeam(stripMascot(selectedGame.away_team), nbaList, 'team');
                const homeT = fuzzyMatchTeam(stripMascot(selectedGame.home_team), nbaList, 'team');
                if(awayT && homeT) {
                  const rows = [
                    {label:'Home Record', away: awayT.home_record||'N/A', home: homeT.home_record||'N/A', desc:'Record at home'},
                    {label:'Away Record', away: awayT.away_record||'N/A', home: homeT.away_record||'N/A', desc:'Record on the road'},
                    {label:'Last 5 Net Rtg', away: (awayT.last_10_net_rating||0).toFixed(1), home: (homeT.last_10_net_rating||0).toFixed(1), awayGood: (awayT.last_10_net_rating||0)>(homeT.last_10_net_rating||0), desc:'Recent form — net rating over last 5 games'},
                    {label:'TOV%', away: (awayT.tov_pct||0).toFixed(1)+'%', home: (homeT.tov_pct||0).toFixed(1)+'%', awayGood: (awayT.tov_pct||0)<(homeT.tov_pct||0), desc:'Lower = fewer turnovers'},
                    {label:'OREB%', away: (awayT.oreb_pct||0).toFixed(1)+'%', home: (homeT.oreb_pct||0).toFixed(1)+'%', awayGood: (awayT.oreb_pct||0)>(homeT.oreb_pct||0), desc:'Offensive rebounding rate'},
                  ];
                  if(awayT.injury_note || homeT.injury_note) {
                    rows.push({label:'Injuries', away: awayT.injury_note||'None', home: homeT.injury_note||'None', desc:'Key injuries', awayGood: false});
                  }
                  return(
                    <View>
                      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
                        <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:13}}>{awayShort}</Text>
                        <View style={{backgroundColor:'rgba(0,229,160,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,borderWidth:1,borderColor:'rgba(0,229,160,0.3)'}}><Text style={{color:'#00e5a0',fontSize:9,fontWeight:'800'}}>📡 LIVE DATA</Text></View>
                        <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:13}}>{homeShort}</Text>
                      </View>
                      {rows.map((row,i)=>(
                        <View key={i} style={{marginBottom:12}}>
                          <Text style={{color:'#4a6070',fontSize:10,fontWeight:'600',marginBottom:2,textAlign:'center'}}>{row.label}</Text>
                          <Text style={{color:'#4a6071',fontSize:9,textAlign:'center',marginBottom:6}}>{row.desc}</Text>
                          <View style={{flexDirection:'row',gap:8}}>
                            <View style={{flex:1,backgroundColor:row.awayGood?'rgba(0,229,160,0.1)':row.awayGood===false?'rgba(255,77,109,0.1)':'rgba(122,146,168,0.1)',borderRadius:10,padding:10,alignItems:'center',borderWidth:1,borderColor:row.awayGood?'rgba(0,229,160,0.3)':row.awayGood===false?'rgba(255,77,109,0.3)':'rgba(122,146,168,0.3)'}}>
                              <Text style={{color:row.awayGood?'#00e5a0':row.awayGood===false?'#ff4d6d':'#e8f0f8',fontWeight:'800',fontSize:14}}>{row.away}</Text>
                            </View>
                            <View style={{flex:1,backgroundColor:row.awayGood===false?'rgba(0,229,160,0.1)':row.awayGood?'rgba(255,77,109,0.1)':'rgba(122,146,168,0.1)',borderRadius:10,padding:10,alignItems:'center',borderWidth:1,borderColor:row.awayGood===false?'rgba(0,229,160,0.3)':row.awayGood?'rgba(255,77,109,0.3)':'rgba(122,146,168,0.3)'}}>
                              <Text style={{color:row.awayGood===false?'#00e5a0':row.awayGood?'#ff4d6d':'#e8f0f8',fontWeight:'800',fontSize:14}}>{row.home}</Text>
                            </View>
                          </View>
                        </View>
                      ))}
                    </View>
                  );
                }
              }
              return(
                <View style={{alignItems:'center',paddingVertical:30}}>
                  <Text style={{fontSize:32}}>📊</Text>
                  <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:16,marginTop:12}}>Situational Data</Text>
                  <Text style={{color:'#7a92a8',fontSize:13,marginTop:8,textAlign:'center',lineHeight:20}}>Advanced situational analytics coming soon for {gamesSport}.</Text>
                  <View style={{marginTop:16,backgroundColor:'rgba(255,184,0,0.1)',borderRadius:12,padding:12,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
                    <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:12,textAlign:'center'}}>🔜 COMING IN V1.1</Text>
                  </View>
                </View>
              );
            })()}
          </View>
        )}
      </View>
    );
  };

  const renderLineChart = (lines, label) => {
    if(!lines||lines.length<2) return null;
    const CHART_W=320; const CHART_H=110;
    const PAD_L=32; const PAD_R=32; const PAD_T=16; const PAD_B=30;
    const innerW=CHART_W-PAD_L-PAD_R;
    const innerH=CHART_H-PAD_T-PAD_B;
    const vals=lines.map(l=>l.point);
    const min=Math.min(...vals); const max=Math.max(...vals);
    const moved=(max-min).toFixed(1);
    const getX=(i)=>lines.length<=1?PAD_L+innerW/2:PAD_L+(i/(lines.length-1))*innerW;
    const getY=(v)=>max===min?PAD_T+innerH/2:PAD_T+((max-v)/(max-min))*innerH;
    const points=lines.map((l,i)=>({x:getX(i),y:getY(l.point),val:l.point,book:l.book}));
    const linePath=points.map((p,i)=>(i===0?'M':'L')+p.x.toFixed(1)+' '+p.y.toFixed(1)).join(' ');
    const areaPath=linePath+' L'+points[points.length-1].x.toFixed(1)+' '+(PAD_T+innerH)+' L'+points[0].x.toFixed(1)+' '+(PAD_T+innerH)+' Z';
    const isHRBBest=points.some(p=>p.book===HRB&&p.val===min);
    const gradColor=parseFloat(moved)===0?'#4a6070':isHRBBest?HRB_COLOR:'#00e5a0';
    const gradId='g_'+label.replace(/\W/g,'');
    return(
      <View style={{marginBottom:16}}>
        <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:6}}>
          <Text style={{color:'#7a92a8',fontSize:11,fontWeight:'700'}}>{label}</Text>
          <View style={{flexDirection:'row',gap:8,alignItems:'center'}}>
            <Text style={{color:'#4a6070',fontSize:10}}>Range: {min} — {max}</Text>
            <Text style={{color:parseFloat(moved)>0?'#ffd166':'#00e5a0',fontSize:11,fontWeight:'700'}}>
              {parseFloat(moved)>0?'↕ '+moved+' pts':'🔒 Locked'}
            </Text>
          </View>
        </View>
        <View style={{backgroundColor:'#0d1520',borderRadius:12,overflow:'hidden',borderWidth:1,borderColor:'#1f2d3d'}}>
          <Svg width={CHART_W} height={CHART_H}>
            <Defs>
              <LinearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <Stop offset="0" stopColor={gradColor} stopOpacity="0.4"/>
                <Stop offset="1" stopColor={gradColor} stopOpacity="0.02"/>
              </LinearGradient>
            </Defs>
            <SvgLine x1={PAD_L} y1={PAD_T} x2={PAD_L} y2={PAD_T+innerH} stroke="#2a3a4a" strokeWidth="1"/>
            <SvgLine x1={PAD_L} y1={PAD_T+innerH} x2={CHART_W-PAD_R} y2={PAD_T+innerH} stroke="#2a3a4a" strokeWidth="1"/>
            <Path d={areaPath} fill={'url(#'+gradId+')'}/>
            <Path d={linePath} stroke={gradColor} strokeWidth="2.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
            {points.map((p,i)=>{
              const isHRB=p.book===HRB;
              const dotColor=isHRB?HRB_COLOR:i===0?'#ffd166':i===points.length-1?'#00e5a0':'#0099ff';
              const r=isHRB?6:4.5;
              const showLabel=isHRB||i===0||i===points.length-1;
              const labelY=i===0?p.y+r+10:p.y-r-4;
              return(
                <React.Fragment key={i}>
                  {isHRB&&<Circle cx={p.x} cy={p.y} r={r+3.5} fill="none" stroke={HRB_COLOR} strokeWidth="1" strokeOpacity="0.4"/>}
                  <Circle cx={p.x} cy={p.y} r={r+1.5} fill="#0d1520"/>
                  <Circle cx={p.x} cy={p.y} r={r} fill={dotColor}/>
                  {showLabel&&<SvgText x={p.x} y={labelY} fontSize="9" fill={isHRB?HRB_COLOR:i===0?'#ffd166':'#00e5a0'} textAnchor="middle" fontWeight={isHRB?'bold':'normal'}>{p.val}</SvgText>}
                  <SvgText x={p.x} y={CHART_H-6} fontSize="8" fill={isHRB?HRB_COLOR:'#4a6070'} textAnchor="middle" fontWeight={isHRB?'bold':'normal'}>
                    {isHRB?'HRB':p.book.split(' ')[0]}
                  </SvgText>
                </React.Fragment>
              );
            })}
          </Svg>
        </View>
        {isHRBBest&&(
          <View style={{flexDirection:'row',alignItems:'center',gap:6,marginTop:6,backgroundColor:'rgba(255,184,0,0.07)',borderRadius:8,padding:6,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
            <Text style={{color:HRB_COLOR,fontSize:11,fontWeight:'700'}}>🎸 Hard Rock has the best {label.toLowerCase()} line!</Text>
          </View>
        )}
      </View>
    );
  };

  const renderLineMovement=(game, histOdds={}, histLoading={})=>{
    if(!game||!game.bookmakers)return null;
    const spreadLines=game.bookmakers.map(bm=>{
      const s=bm.markets&&bm.markets.find(m=>m.key==='spreads');
      if(!s||!s.outcomes||!s.outcomes[0])return null;
      return{book:BOOKMAKER_MAP[bm.key]||bm.key,point:s.outcomes[0].point};
    }).filter(x=>x!==null);
    const totalLines=game.bookmakers.map(bm=>{
      const t=bm.markets&&bm.markets.find(m=>m.key==='totals');
      if(!t||!t.outcomes||!t.outcomes[0])return null;
      return{book:BOOKMAKER_MAP[bm.key]||bm.key,point:t.outcomes[0].point};
    }).filter(x=>x!==null);
    if(spreadLines.length<2&&totalLines.length<2)return null;
    return(
      <View style={{backgroundColor:'#0a1018',borderRadius:14,padding:14,marginBottom:16,borderWidth:1,borderColor:'#1f2d3d'}}>
        <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:13,marginBottom:14}}>📊 LINE MOVEMENT</Text>{(()=>{
          const key = game?.id||(game?.away_team+game?.home_team);
          const hist = histOdds?.[key]||null;
          const loading = histLoading?.[key]||false;
          if(loading) return <Text style={{color:'#4a6070',fontSize:11,marginBottom:8}}>⏳ Loading opening lines...</Text>;
          if(!hist||(!hist.openingSpread&&!hist.openingTotal&&!hist.openingML)) return null;
          return(
            <View style={{flexDirection:'row',gap:8,marginBottom:12,flexWrap:'wrap'}}>
              {hist.openingSpread!=null&&<View style={{backgroundColor:'rgba(255,184,0,0.08)',borderRadius:8,paddingHorizontal:10,paddingVertical:6,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
                <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>OPENING SPREAD</Text>
                <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14}}>{hist.openingSpread>0?'+':''}{hist.openingSpread}</Text>
              </View>}
              {hist.openingTotal!=null&&<View style={{backgroundColor:'rgba(255,184,0,0.08)',borderRadius:8,paddingHorizontal:10,paddingVertical:6,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
                <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>OPENING TOTAL</Text>
                <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14}}>{hist.openingTotal}</Text>
              </View>}
              {hist.openingML!=null&&<View style={{backgroundColor:'rgba(255,184,0,0.08)',borderRadius:8,paddingHorizontal:10,paddingVertical:6,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
                <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>OPENING ML</Text>
                <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14}}>{hist.openingML>0?'+':''}{hist.openingML}</Text>
              </View>}
            </View>
          );
        })()}
        {renderLineChart(spreadLines,'SPREAD')}
        {renderLineChart(totalLines,'TOTAL (O/U)')}
        <View style={{flexDirection:'row',gap:12,paddingTop:8,borderTopWidth:1,borderTopColor:'#1f2d3d',flexWrap:'wrap'}}>
          <View style={{flexDirection:'row',alignItems:'center',gap:4}}><View style={{width:10,height:10,borderRadius:5,backgroundColor:HRB_COLOR}}/><Text style={{color:HRB_COLOR,fontSize:10,fontWeight:'700'}}>Hard Rock</Text></View>
          <View style={{flexDirection:'row',alignItems:'center',gap:4}}><View style={{width:8,height:8,borderRadius:4,backgroundColor:'#ffd166'}}/><Text style={{color:'#7a92a8',fontSize:10}}>Open</Text></View>
          <View style={{flexDirection:'row',alignItems:'center',gap:4}}><View style={{width:8,height:8,borderRadius:4,backgroundColor:'#0099ff'}}/><Text style={{color:'#7a92a8',fontSize:10}}>Mid</Text></View>
          <View style={{flexDirection:'row',alignItems:'center',gap:4}}><View style={{width:8,height:8,borderRadius:4,backgroundColor:'#00e5a0'}}/><Text style={{color:'#7a92a8',fontSize:10}}>Current</Text></View>
        </View>
      </View>
    );
  };

  const saveBet=()=>{
    if(!form.matchup||!form.pick){Alert.alert('Missing Info','Please enter a matchup and pick.');return;}
    setBets(prev=>[{...form,id:Date.now(),date:new Date().toLocaleDateString('en-US',{month:'short',day:'numeric'})},...prev]);
    setForm({matchup:'',pick:'',sport:'NBA',type:'Spread',odds:'',units:'',book:'Hard Rock',result:'Pending'});
    setModalVisible(false);
  };
  const deleteBet=(id)=>Alert.alert('Delete Pick','Are you sure?',[
    {text:'Cancel',style:'cancel'},{text:'Delete',style:'destructive',onPress:()=>setBets(prev=>prev.filter(b=>b.id!==id))}
  ]);
  const openEditModal=(bet)=>{setEditingBet({...bet});setEditModalVisible(true);};
  const saveEdit = async () => {
  if(!editingBet.matchup||!editingBet.pick){Alert.alert('Missing Info','Please enter details.');return;}
  const prev = bets.find(b => b.id === editingBet.id);
  const resultChanged = prev && prev.result !== editingBet.result && editingBet.result !== 'Pending';
  
  setBets(p => p.map(b => b.id === editingBet.id ? editingBet : b));
  setEditModalVisible(false);
  setEditingBet(null);

  if(resultChanged) {
    fetchPickRecap(editingBet, editingBet.result);
    const units = parseFloat(editingBet.units) || 1;
    const odds = parseFloat(editingBet.odds) || -110;
    const profitLoss = editingBet.result === 'Win'
      ? odds > 0 ? units * (odds/100) : units * (100/Math.abs(odds))
      : editingBet.result === 'Loss' ? -units : 0;
    try {
      await supabase.from('outcomes').insert({
        sport: editingBet.sport,
        bet_type: editingBet.type,
        matchup: editingBet.matchup,
        pick: editingBet.pick,
        odds: odds,
        units: units,
        result: editingBet.result,
        profit_loss: profitLoss,
        sweat_score: editingBet.sweatScore || null,
        model_lean: editingBet.modelLean || null,
        book: editingBet.book || null,
        is_parlay: editingBet.type === 'Parlay',
        user_id: 'beta_user',
      });
    } catch(e) { console.log('Supabase log error:', e.message); }
  }
};
 const quickUpdateResult = async (id, result) => {
  const bet = bets.find(b => b.id === id);
  if(!bet) return;
  setBets(prev => prev.map(b => b.id === id ? {...b, result} : b));

  if(result !== 'Pending') {
    fetchPickRecap(bet, result);
    const units = parseFloat(bet.units) || 1;
    const odds = parseFloat(bet.odds) || -110;
    const profitLoss = result === 'Win'
      ? odds > 0 ? units * (odds/100) : units * (100/Math.abs(odds))
      : result === 'Loss' ? -units : 0;
    try {
      await supabase.from('outcomes').insert({
        sport: bet.sport,
        bet_type: bet.type,
        matchup: bet.matchup,
        pick: bet.pick,
        odds: odds,
        units: units,
        result: result,
        profit_loss: profitLoss,
        sweat_score: bet.sweatScore || null,
        model_lean: bet.modelLean || null,
        book: bet.book || null,
        is_parlay: bet.type === 'Parlay',
        user_id: 'beta_user',
      });
    } catch(e) { console.log('Supabase log error:', e.message); }
  }
};
  const addToParlay=(game,pick,oddsRaw)=>{
    const isNeg=oddsRaw<0;
    const leg={id:Date.now(),matchup:game.away_team+' vs '+game.home_team,pick,odds:String(Math.abs(oddsRaw)),oddsSign:isNeg?'-':'+'};
    setParlayLegs(prev=>{
      if(prev.some(l=>l.matchup===leg.matchup&&l.pick===leg.pick)){showToast('⚠️ Already in parlay');return prev;}
      showToast('✅ Added: '+pick);return[...prev,leg];
    });
  };
  const addLeg=()=>{
    if(!legForm.matchup||!legForm.pick||!legForm.odds){Alert.alert('Missing Info','Fill in all fields.');return;}
    setParlayLegs(prev=>[...prev,{id:Date.now(),...legForm}]);
    setLegForm({matchup:'',pick:'',odds:'',oddsSign:'-'});setAddLegModal(false);
  };
  const removeLeg=(id)=>setParlayLegs(prev=>prev.filter(l=>l.id!==id));
  const parlayDecimal=calcParlayOdds(parlayLegs);
  const parlayAmerican=parlayLegs.length>0?decimalToAmerican(parlayDecimal):'--';
  const parlayPayout=parlayLegs.length>0?(parseFloat(parlayWager||0)*parlayDecimal).toFixed(2):'0.00';
  const parlayProfit=parlayLegs.length>0?(parseFloat(parlayPayout)-parseFloat(parlayWager||0)).toFixed(2):'0.00';
  const parlayProb=parlayLegs.length>0?impliedProb(parlayDecimal):'0.0';
  const getBestPropLine=(lines)=>lines&&lines.length?lines.reduce((best,l)=>(!best||l.line<best.line)?l:best,null):null;
  const openGameDetail=(game)=>{
  const scoreData = sweatScores[game.id] || calcGameSweatScore(game, gamesSport, fanmatchData, mlbGameContext, nbaTeamData);
  fetchAltLines(game, gamesSport);
  if(!sweatScores[game.id]) {
    setSweatScores(prev => ({...prev, [game.id]: scoreData}));
  }
  setSelectedGame(game);
  setMatchupTab('money');
  setScheduleTeam('away');
  setSitMarket('spread');
  setStatView('offense');
  setGameDetailModal(true);
  fetchHistoricalOdds(game, gamesSport);
  fetchGameNarrative(game, scoreData);
};
  const logPickFromGame=(game,pick)=>{
    setForm({matchup:game.away_team+' vs '+game.home_team,pick,sport:gamesSport,type:'Spread',odds:'',units:'',book:'Hard Rock',result:'Pending'});
    setGameDetailModal(false);setModalVisible(true);
  };

  if(!betsLoaded){
    return(<View style={[styles.container,{alignItems:'center',justifyContent:'center'}]}><ActivityIndicator size="large" color={HRB_COLOR}/><Text style={{color:'#7a92a8',marginTop:12}}>Loading The Sweat Locker...</Text></View>);
  }

  const trends=myTrends();

  return(
    <View style={styles.container}>
      {!onboardingDone&&(
        <View style={{position:'absolute',top:0,left:0,right:0,bottom:0,backgroundColor:'#060d14',zIndex:999,justifyContent:'space-between',padding:30,paddingTop:80,paddingBottom:50}}>
          {onboardingStep===0&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>🔒</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:34,textAlign:'center',marginBottom:12,letterSpacing:1}}>THE SWEAT LOCKER</Text>
              <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:16,textAlign:'center',marginBottom:20}}>Bet Smarter, Sweat Less.</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>A real sports analytics engine — not picks, not vibes. Proprietary models updated twice daily.</Text>
            </View>
          )}
          {onboardingStep===1&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>🔥</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:30,textAlign:'center',marginBottom:12}}>Sweat Score</Text>
              <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:16,textAlign:'center',marginBottom:20}}>Every game graded 0–100</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>Built on pitcher Statcast data, NBA tracking stats, market efficiency, sharp money movement, and weather. Higher scores = stronger model conviction.{'\n\n'}Updates at 8am and 2pm ET daily.</Text>
            </View>
          )}
          {onboardingStep===2&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>🏟</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:30,textAlign:'center',marginBottom:12}}>Games Tab</Text>
              <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:16,textAlign:'center',marginBottom:20}}>Your daily command center</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>Every game card shows its Sweat Score and top model signals at a glance.{'\n\n'}Tap any game for the full breakdown:{'\n'}📚 Book consensus across 7+ sportsbooks{'\n'}🎤 Jerry's AI-powered game read{'\n'}📊 Real team stats and pitcher matchups{'\n'}📈 Line movement with opening vs current{'\n'}⚾ NRFI score and first inning prediction{'\n\n'}Add any line to Parlay Builder with one tap.</Text>
            </View>
          )}
          {onboardingStep===3&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>🧠</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:30,textAlign:'center',marginBottom:12}}>Meet Jerry</Text>
              <Text style={{color:'#00e5a0',fontWeight:'700',fontSize:16,textAlign:'center',marginBottom:20}}>Your AI sports analyst</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>Jerry lives in the Jerry tab. Four tools:{'\n\n'}🎯 Prop Jerry — EV scanner across MLB, NBA, NHL, UFC{'\n'}🎰 Daily Degen — Jerry's analytically backed 3-4 leg parlay{'\n'}🚫 Jerry's Fades — plays to avoid today{'\n'}📊 My Record — Jerry's verified model performance</Text>
            </View>
          )}
          {onboardingStep===4&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>⚾</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:30,textAlign:'center',marginBottom:12}}>NRFI Model</Text>
              <Text style={{color:'#00e5a0',fontWeight:'700',fontSize:16,textAlign:'center',marginBottom:20}}>No Run First Inning — our flagship</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>Built on pitcher xERA, strikeout rate vs lineup K%, ground ball rate, first inning splits, days rest, weather, park factor, umpire tendencies, and offensive quality.{'\n\n'}Calibrated on 2,400+ games of real outcome data. Model updates twice daily.{'\n\n'}Scores 85+ are our highest confidence plays. The 90-94 sweet spot has historically been our best range.</Text>
            </View>
          )}
          {onboardingStep===5&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>⏰</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:30,textAlign:'center',marginBottom:12}}>Best Times to Check</Text>
              <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:16,textAlign:'center',marginBottom:20}}>Pipeline runs twice daily</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>8am ET — NRFI scores, game context, pitchers confirmed{'\n\n'}2pm ET — Lineups confirmed, umpires assigned, Prop Jerry most accurate, Play of the Day locked in{'\n\n'}After 2pm — All data locked in. Best time for full analysis.</Text>
            </View>
          )}
          {onboardingStep===6&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>⚠️</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:30,textAlign:'center',marginBottom:12}}>One More Thing</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>The Sweat Locker provides data analysis for entertainment purposes only.{'\n\n'}Past performance is not indicative of future results.{'\n\n'}Must be 18+ to use this app. Know your local laws and gamble responsibly.</Text>
            </View>
          )}
          <View style={{flexDirection:'row',justifyContent:'center',gap:8,marginBottom:28}}>
            {[0,1,2,3,4,5,6].map(i=>(
              <View key={i} style={{width:i===onboardingStep?24:8,height:8,borderRadius:4,backgroundColor:i===onboardingStep?HRB_COLOR:'#1f2d3d'}}/>
            ))}
          </View>
          <View style={{gap:12}}>
            {onboardingStep<6?(
              <TouchableOpacity
                style={{backgroundColor:HRB_COLOR,borderRadius:14,padding:18,alignItems:'center'}}
                onPress={()=>setOnboardingStep(s=>s+1)}
              >
                <Text style={{color:'#000',fontWeight:'900',fontSize:17}}>Next →</Text>
              </TouchableOpacity>
            ):(
              <TouchableOpacity
                style={{backgroundColor:HRB_COLOR,borderRadius:14,padding:18,alignItems:'center'}}
                onPress={async()=>{
                  await AsyncStorage.setItem('sweatlocker_onboarded','true');
                  setOnboardingDone(true);
                }}
              >
                <Text style={{color:'#000',fontWeight:'900',fontSize:17}}>Let's Sweat 🔒</Text>
              </TouchableOpacity>
            )}
            {onboardingStep>0&&(
              <TouchableOpacity
                style={{alignItems:'center',padding:10}}
                onPress={()=>setOnboardingStep(s=>s-1)}
              >
                <Text style={{color:'#4a6070',fontSize:13}}>← Back</Text>
              </TouchableOpacity>
            )}
            {onboardingStep<6&&(
              <TouchableOpacity
                style={{alignItems:'center',padding:10}}
                onPress={async()=>{
                  await AsyncStorage.setItem('sweatlocker_onboarded','true');
                  setOnboardingDone(true);
                }}
              >
                <Text style={{color:'#4a6070',fontSize:13}}>Skip</Text>
              </TouchableOpacity>
            )}
          </View>
        </View>
      )}
      {toastVisible&&(<View style={styles.toast}><Text style={styles.toastText}>{toastMsg}</Text></View>)}
      {parlayAnalysisVisible&&(
  <View style={{position:'absolute',bottom:90,left:16,right:16,backgroundColor:'#0d1f2d',borderRadius:16,borderWidth:1,borderColor:HRB_COLOR,zIndex:998,shadowColor:'#000',shadowOffset:{width:0,height:4},shadowOpacity:0.4,shadowRadius:8,maxHeight:'70%'}}>
    <ScrollView>
      <View style={{padding:16}}>
        <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
          <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:12}}>🎤 JERRY'S PARLAY GRADER</Text>
          <TouchableOpacity onPress={()=>setParlayAnalysisVisible(false)}>
            <Text style={{color:'#4a6070',fontSize:13}}>✕ Close</Text>
          </TouchableOpacity>
        </View>
        {parlayAnalysisLoading?(
          <View style={{flexDirection:'row',alignItems:'center',gap:8,paddingVertical:12}}>
            <ActivityIndicator size='small' color={HRB_COLOR}/>
            <Text style={{color:'#4a6070',fontSize:13}}>Jerry is grading each leg...</Text>
          </View>
        ) : parlayAnalysis?.error ? (
          <Text style={{color:'#c8d8e8',fontSize:13,lineHeight:20,fontStyle:'italic'}}>{parlayAnalysis.error}</Text>
        ) : parlayAnalysis?.legs ? (
          <View>
            {/* Correlation Warning */}
            {parlayAnalysis?.hasCorrelation && (
              <View style={{backgroundColor:'rgba(255,77,109,0.1)',borderRadius:10,padding:10,marginBottom:12,borderWidth:1,borderColor:'rgba(255,77,109,0.3)'}}>
                <Text style={{color:'#ff4d6d',fontWeight:'800',fontSize:12}}>⚠️ CORRELATION WARNING</Text>
                <Text style={{color:'#7a92a8',fontSize:11,marginTop:4}}>One or more legs are from the same game — this affects true parlay odds. Some books void correlated parlays.</Text>
              </View>
            )}
            {/* Overall Grade */}
            <View style={{backgroundColor:'rgba(255,184,0,0.08)',borderRadius:12,padding:12,marginBottom:12,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
  <View style={{flexDirection:'row',alignItems:'center',marginBottom:8}}>
    <Text style={{color:'#7a92a8',fontSize:10,fontWeight:'700',letterSpacing:1,flex:1}}>OVERALL GRADE</Text>
    <View style={{width:36,height:36,borderRadius:18,borderWidth:2,borderColor:parlayAnalysis.overallColor||HRB_COLOR,alignItems:'center',justifyContent:'center'}}>
      <Text style={{color:parlayAnalysis.overallColor||HRB_COLOR,fontSize:14,fontWeight:'800'}}>{parlayAnalysis.overallGrade}</Text>
    </View>
  </View>
  <Text style={{color:'#e8f0f8',fontSize:13,lineHeight:18,fontStyle:'italic'}}>"{(parlayAnalysis.verdict||'').replace(/[\[\]{}*_`#]/g, '').trim()}"</Text>
</View>
            {/* Leg Grades */}
            {parlayAnalysis.legs.map((leg, i) => (
              <View key={i} style={{backgroundColor:'#0a1520',borderRadius:12,padding:12,marginBottom:8,borderWidth:1,borderColor:leg.gradeColor+'44',borderLeftWidth:3,borderLeftColor:leg.gradeColor}}>
                <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start',marginBottom:6}}>
                  <View style={{flex:1,marginRight:8}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13}}>{leg.pick}</Text>
                    <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>Leg {leg.leg}</Text>
                  </View>
                  <View style={{alignItems:'center'}}>
                    <View style={{width:36,height:36,borderRadius:18,borderWidth:2,borderColor:leg.gradeColor,alignItems:'center',justifyContent:'center'}}>
                      <Text style={{color:leg.gradeColor,fontSize:16,fontWeight:'800'}}>{leg.grade}</Text>
                    </View>
                    <Text style={{color:'#4a6070',fontSize:9,marginTop:2}}>{leg.confidence}%</Text>
                  </View>
                </View>
                <Text style={{color:'#c8d8e8',fontSize:12,lineHeight:17,fontStyle:'italic',marginBottom:4,flexWrap:'wrap',flex:1}}>"{(leg.jerry||'').replace(/[\[\]{}*_`#]/g, '').trim()}"</Text>
                {leg.risk&&(
                  <View style={{flexDirection:'row',alignItems:'center',gap:4,marginTop:4}}>
                    <Text style={{color:'#ff4d6d',fontSize:10}}>⚠️</Text>
                    <Text style={{color:'#ff4d6d',fontSize:11,flexWrap:'wrap',flex:1}}>{leg.risk}</Text>
                  </View>
                )}
                {(i === parlayAnalysis.strongestLeg - 1) && (
                  <View style={{backgroundColor:'rgba(0,229,160,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,alignSelf:'flex-start',marginTop:6}}>
                    <Text style={{color:'#00e5a0',fontSize:10,fontWeight:'700'}}>💪 STRONGEST LEG</Text>
                  </View>
                )}
                {(i === parlayAnalysis.weakestLeg - 1) && (
                  <View style={{backgroundColor:'rgba(255,77,109,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,alignSelf:'flex-start',marginTop:6}}>
                    <Text style={{color:'#ff4d6d',fontSize:10,fontWeight:'700'}}>⚠️ WEAKEST LEG</Text>
                  </View>
                )}
                {(leg.correlation === 'HIGH' || leg.correlation === 'MODERATE') && (
                  <View style={{backgroundColor:'rgba(255,140,0,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,alignSelf:'flex-start',marginTop:6}}>
                    <Text style={{color:'#ff8c00',fontSize:10,fontWeight:'700'}}>🔗 {leg.correlation} CORRELATION</Text>
                  </View>
                )}
                {leg.pipelineData && (
                  <View style={{backgroundColor:'rgba(0,229,160,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,alignSelf:'flex-start',marginTop:6}}>
                    <Text style={{color:'#00e5a0',fontSize:10,fontWeight:'700'}}>📡 PIPELINE DATA</Text>
                  </View>
                )}
              </View>
            ))}
          </View>
        ) : null}
      </View>
    </ScrollView>
  </View>
)}
      {pickRecapVisible&&(
        <TouchableOpacity
          onPress={()=>setPickRecapVisible(false)}
          style={{position:'absolute',bottom:90,left:16,right:16,backgroundColor:'#0d1f2d',borderRadius:16,padding:16,borderWidth:1,borderColor:HRB_COLOR,zIndex:998,shadowColor:'#000',shadowOffset:{width:0,height:4},shadowOpacity:0.4,shadowRadius:8}}
        >
          <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:11,marginBottom:6}}>🎤 JERRY'S TAKE</Text>
          <Text style={{color:'#e8f0f8',fontSize:13,lineHeight:20,fontStyle:'italic'}}>"{pickRecap}"</Text>
          <Text style={{color:'#4a6070',fontSize:10,marginTop:8,textAlign:'right'}}>Tap to dismiss</Text>
        </TouchableOpacity>
      )}
      <View style={styles.header}>
        <View>
          <Text style={styles.logo}>🔒 THE SWEAT LOCKER</Text>
          <Text style={{color:HRB_COLOR,fontSize:10,fontWeight:'700',letterSpacing:1}}>BET SMARTER, SWEAT LESS.</Text>
        </View>
        <View style={{flexDirection:'row',alignItems:'center',gap:8}}>
          {parlayLegs.length>0&&(<TouchableOpacity style={styles.parlayBadge} onPress={()=>setActiveTab('parlay')}><Text style={styles.parlayBadgeText}>{parlayLegs.length} 🎰</Text></TouchableOpacity>)}
         <TouchableOpacity style={styles.navIcon} onPress={()=>setSettingsModal(true)}><Text style={{fontSize:18}}>⚙️</Text></TouchableOpacity>
        </View>
      </View>

      <ScrollView style={styles.content} showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={HRB_COLOR}/>}>

        {activeTab==='home'&&(
          <View>
           <View style={{marginBottom:12}}>

           {/* DAILY BEST BET */}
{(dailyBestBetLoading || dailyBestBet) && (
  <View style={{backgroundColor:'#0a1520',borderRadius:16,padding:16,borderWidth:1.5,borderColor:HRB_COLOR,marginBottom:16,shadowColor:HRB_COLOR,shadowOffset:{width:0,height:2},shadowOpacity:0.3,shadowRadius:8}}>
    <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
      <View style={{flexDirection:'row',alignItems:'center',gap:8}}>
        <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:12,letterSpacing:1}}>🔒 JERRY'S PLAY OF THE DAY</Text>
        <View style={{backgroundColor:'rgba(255,184,0,0.15)',borderRadius:6,paddingHorizontal:6,paddingVertical:2}}>
          <Text style={{color:HRB_COLOR,fontSize:9,fontWeight:'800'}}>TODAY</Text>
        </View>
      </View>
      {dailyBestBet?.score && (
        <View style={{
  backgroundColor:dailyBestBet.isPrime?'rgba(255,184,0,0.15)':dailyBestBet.label==='STRONG LEAN'?'rgba(0,229,160,0.1)':'rgba(0,153,255,0.1)',
  borderRadius:8,paddingHorizontal:8,paddingVertical:3,borderWidth:1,
  borderColor:dailyBestBet.isPrime?HRB_COLOR:dailyBestBet.label==='STRONG LEAN'?'#00e5a0':'#0099ff'
}}>
  <Text style={{
    color:dailyBestBet.isPrime?HRB_COLOR:dailyBestBet.label==='STRONG LEAN'?'#00e5a0':'#0099ff',
    fontSize:10,fontWeight:'800'
  }}>{dailyBestBet.score?.total || dailyBestBet.score} {dailyBestBet.label}</Text>
</View>
      )}
    </View>

    {dailyBestBetLoading ? (
      <View style={{flexDirection:'row',alignItems:'center',gap:8,paddingVertical:8}}>
        <ActivityIndicator size='small' color={HRB_COLOR}/>
        <Text style={{color:'#4a6070',fontSize:13}}>Jerry is finding today's best play...</Text>
      </View>
    ) : dailyBestBet?.waiting ? (
      <Text style={{color:'#7a92a8',fontSize:13,lineHeight:20}}>Jerry's Play of the Day generates after the morning pipeline runs. Data locks in at 10am ET with pitcher matchups and NRFI scores, then refreshes at 2pm ET with confirmed lineups and umpires.</Text>
    ) : dailyBestBet?.noGames ? (
      <Text style={{color:'#7a92a8',fontSize:13}}>No games on the slate today. Check back tomorrow.</Text>
    ) : dailyBestBet?.noPrime ? (
      <Text style={{color:'#7a92a8',fontSize:13}}>No prime plays today — top game scores {dailyBestBet.topScore}/100. Jerry says wait for a better spot.</Text>
    ) : dailyBestBet?.game ? (
      <View>
{isPlayoffMode && dailyBestBet?.sport === 'NBA' && (
  <View style={{backgroundColor:'rgba(255,184,0,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,marginBottom:8,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
    <Text style={{color:'#FFB800',fontSize:11,fontWeight:'700'}}>🏆 NBA PLAYOFFS</Text>
  </View>
)}
        <Text style={{color:'#7a92a8',fontSize:11,fontWeight:'700',letterSpacing:0.5,marginBottom:6}}>
          {SPORT_EMOJI[dailyBestBet.sport] || '🎯'} {dailyBestBet.sport} — {new Date(dailyBestBet.game.commence_time).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true})} ET
        </Text>
        <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:15,marginBottom:8}}>
          {dailyBestBet.game.away_team} @ {dailyBestBet.game.home_team}
        </Text>
        <View style={{flexDirection:'row',alignItems:'center',gap:10,marginBottom:12}}>
          <View style={{backgroundColor:'rgba(255,184,0,0.12)',borderRadius:10,paddingHorizontal:12,paddingVertical:8,borderWidth:1,borderColor:HRB_COLOR,flex:1}}>
            <Text style={{color:'#7a92a8',fontSize:9,fontWeight:'700',letterSpacing:1,marginBottom:2}}>TOP PLAY</Text>
            <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:16}}>
  {dailyBestBet.leanDisplay || 'Top Model Edge'}
</Text>
          </View>
          <View style={{backgroundColor:'rgba(0,229,160,0.1)',borderRadius:10,paddingHorizontal:12,paddingVertical:8,borderWidth:1,borderColor:'rgba(0,229,160,0.3)',alignItems:'center'}}>
            <Text style={{color:'#7a92a8',fontSize:9,fontWeight:'700',letterSpacing:1,marginBottom:2}}>SWEAT</Text>
            <Text style={{color:'#00e5a0',fontWeight:'800',fontSize:16}}>{dailyBestBet.score?.total || '--'}</Text>
          </View>
          <View style={{backgroundColor:'rgba(0,153,255,0.1)',borderRadius:10,paddingHorizontal:12,paddingVertical:8,borderWidth:1,borderColor:'rgba(0,153,255,0.3)',alignItems:'center'}}>
            <Text style={{color:'#7a92a8',fontSize:9,fontWeight:'700',letterSpacing:1,marginBottom:2}}>BOOK</Text>
            <Text style={{color:'#0099ff',fontWeight:'800',fontSize:11}}>🎸 HRB</Text>
          </View>
        </View>
        <Text style={{color:'#c8d8e8',fontSize:13,lineHeight:20,fontStyle:'italic'}}>"{dailyBestBet.narrative?.replace(/#{1,6}\s/g, '').replace(/\*\*/g, '').replace(/\*/g, '').trim()}"</Text>
      </View>
    ) : null}
  </View>
)}

  {/* Jerry Daily Briefing */}
  <View style={{backgroundColor:'rgba(255,184,0,0.06)',borderRadius:14,padding:14,borderWidth:1,borderColor:'rgba(255,184,0,0.2)',marginBottom:16}}>
                <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:12,marginBottom:8}}>🎤 JERRY'S MORNING READ</Text>
                {dailyBriefingLoading?(
                  <View style={{flexDirection:'row',alignItems:'center',gap:8}}>
                    <ActivityIndicator size='small' color={HRB_COLOR}/>
                    <Text style={{color:'#4a6070',fontSize:13}}>Jerry is breaking down today's slate...</Text>
                  </View>
                ):(
                  <Text style={{color:'#c8d8e8',fontSize:13,lineHeight:20,fontStyle:'italic'}}>{dailyBriefing}</Text>
                )}
              </View>
                {trackingMode==='dollars'&&(
                <TouchableOpacity
                  onPress={()=>{setTempUnitSize(unitSize);setUnitSizeModal(true);}}
                  style={{flexDirection:'row',alignItems:'center',justifyContent:'space-between',backgroundColor:'rgba(255,184,0,0.08)',borderWidth:1,borderColor:'rgba(255,184,0,0.2)',borderRadius:10,paddingHorizontal:14,paddingVertical:10,marginBottom:12}}
                >
                  <Text style={{color:'#7a92a8',fontSize:13}}>Unit Size</Text>
                  <Text style={{color:HRB_COLOR,fontSize:13,fontWeight:'700'}}>${unitSize} / unit ›</Text>
                </TouchableOpacity>
              )}

              <View style={{flexDirection:'row',alignItems:'center',justifyContent:'space-between',marginBottom:8}}>
                <View style={{flexDirection:'row',alignItems:'center',gap:8}}>
                <TouchableOpacity onPress={toggleMode} style={{flexDirection:'row',backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',borderRadius:20,padding:3,gap:2}}>
                  <View style={[{paddingHorizontal:10,paddingVertical:4,borderRadius:16},trackingMode==='units'&&{backgroundColor:'#00e5a0'}]}>
                    <Text style={{fontSize:11,fontWeight:'700',color:trackingMode==='units'?'#080c10':'#7a92a8'}}>Units</Text>
                  </View>
                  <View style={[{paddingHorizontal:10,paddingVertical:4,borderRadius:16},trackingMode==='dollars'&&{backgroundColor:'#00e5a0'}]}>
                    <Text style={{fontSize:11,fontWeight:'700',color:trackingMode==='dollars'?'#080c10':'#7a92a8'}}>$$$</Text>
                  </View>
                </TouchableOpacity>
              </View>
            </View>
            </View>
            <TouchableOpacity
                onPress={async()=>{await autoDetectResults();}}
                style={{flexDirection:'row',alignItems:'center',justifyContent:'flex-end',gap:6,marginBottom:8,paddingHorizontal:4}}
              >
                <Text style={{color:'#4a6070',fontSize:11}}>🔄 Sync Results</Text>
              </TouchableOpacity>

            <View style={styles.hero}>
              <View>
                <Text style={styles.heroSub}>Season Record</Text>
                <Text style={styles.heroRecord}>{wins}-{losses}-{pushes}</Text>
                <Text style={styles.heroMeta}>Win Rate: {winRate}% • {trackingMode==='units'?(totalUnits>=0?'+':'')+totalUnits.toFixed(1)+'u':(totalDollars>=0?'+':'-')+'$'+Math.abs(totalDollars).toFixed(0)}</Text>
              </View>
              <View style={styles.roiCircle}>
                <Text style={styles.roiVal}>{trackingMode==='units'?(totalUnits>=0?'+':'')+totalUnits.toFixed(1)+'u':(totalDollars>=0?'+':'-')+'$'+Math.abs(totalDollars).toFixed(0)}</Text>
                <Text style={styles.roiLbl}>{trackingMode==='units'?'UNITS':'Profit'}</Text>
              </View>
            </View>
            <View style={styles.statRow}>
              <View style={[styles.statBox,styles.statGreen]}><Text style={[styles.statVal,{color:'#00e5a0'}]}>{winRate}%</Text><Text style={styles.statKey}>Win Rate</Text></View>
              <View style={[styles.statBox,styles.statBlue]}><Text style={[styles.statVal,{color:'#0099ff',fontSize:trackingMode==='dollars'?16:20}]}>{trackingMode==='units'?(totalUnits>=0?'+':'')+totalUnits.toFixed(1)+'u':(totalDollars>=0?'+':'-')+'$'+Math.abs(totalDollars).toFixed(0)}</Text><Text style={styles.statKey}>{trackingMode==='units'?'UNITS':'Profit'}</Text></View>
              <View style={styles.statBox}><Text style={styles.statVal}>{bets.length}</Text><Text style={styles.statKey}>Total Bets</Text></View>
            </View>
            {/* ROI CHART */}
            <View style={[styles.card,{marginBottom:16}]}>
              <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
                <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:15}}>📈 ROI Tracker</Text>
                <TouchableOpacity onPress={()=>setRoiUnit(roiUnit==='units'?'dollars':'units')} style={{backgroundColor:'rgba(255,184,0,0.1)',borderRadius:8,paddingHorizontal:10,paddingVertical:4,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
                  <Text style={{color:HRB_COLOR,fontSize:11,fontWeight:'700'}}>{roiUnit==='units'?'Units':'$$$'}</Text>
                </TouchableOpacity>
              </View>
              {/* Tab selector */}
              <View style={{flexDirection:'row',gap:6,marginBottom:12}}>
                {[{id:'cumulative',label:'📈 P/L'},{id:'sports',label:'🏆 By Sport'}].map(t=>(
                  <TouchableOpacity key={t.id} style={{flex:1,alignItems:'center',paddingVertical:7,borderRadius:10,borderWidth:1,borderColor:roiChartTab===t.id?HRB_COLOR:'#1f2d3d',backgroundColor:roiChartTab===t.id?'rgba(255,184,0,0.1)':'#151c24'}} onPress={()=>setRoiChartTab(t.id)}>
                    <Text style={{color:roiChartTab===t.id?HRB_COLOR:'#7a92a8',fontSize:12,fontWeight:'700'}}>{t.label}</Text>
                  </TouchableOpacity>
                ))}
              </View>
              {roiChartTab==='cumulative'&&(()=>{
                const data = calcROIData(bets, roiTimeRange, roiUnit, unitSize);
                //console.log('ROI LAST:', data[data.length-1]?.value, '| DATA LENGTH:', data.length);
                if(data.length === 0) return(
                  <View style={{alignItems:'center',paddingVertical:24}}>
                    <Text style={{fontSize:28}}>📊</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,marginTop:8}}>Log settled bets to see your ROI chart</Text>
                  </View>
                );
                const maxVal = Math.max(...data.map(d=>d.value), 0.1);
                const minVal = Math.min(...data.map(d=>d.value), -0.1);
                const range = maxVal - minVal || 1;
                const chartH = 120;
                const chartW = 280;
                const lastVal = data[data.length-1]?.value || 0;
                const isPositive = lastVal >= 0;
                return(
                  <View>
                    {/* Time range toggle */}
                    <View style={{flexDirection:'row',gap:4,marginBottom:12}}>
                      {[{id:'last10',label:'L10'},{id:'last30',label:'L30'},{id:'all',label:'All'}].map(t=>(
                        <TouchableOpacity key={t.id} style={{paddingHorizontal:12,paddingVertical:5,borderRadius:8,borderWidth:1,borderColor:roiTimeRange===t.id?HRB_COLOR:'#1f2d3d',backgroundColor:roiTimeRange===t.id?'rgba(255,184,0,0.1)':'transparent'}} onPress={()=>setRoiTimeRange(t.id)}>
                          <Text style={{color:roiTimeRange===t.id?HRB_COLOR:'#7a92a8',fontSize:11,fontWeight:'700'}}>{t.label}</Text>
                        </TouchableOpacity>
                      ))}
                      <View style={{flex:1,alignItems:'flex-end',justifyContent:'center'}}>
                        <Text style={{color:isPositive?'#00e5a0':'#ff4d6d',fontWeight:'800',fontSize:16}}>{isPositive?'+':''}{lastVal.toFixed(1)}{roiUnit==='units'?'u':'$'}</Text>
                      </View>
                    </View>
                    {/* SVG Chart */}
<Svg width={chartW} height={chartH+20} style={{alignSelf:'center'}}>
  {/* Zero line */}
  {(()=>{
    const zeroY = chartH - ((0-minVal)/range*chartH);
    return <SvgLine x1={0} y1={zeroY} x2={chartW} y2={zeroY} stroke="#2a3a4a" strokeWidth={1} strokeDasharray="4,4"/>;
  })()}
  {/* Area fill */}
  {data.length > 1 && (()=>{
    const pts = data.map((d,i)=>{
      const x = (i/(data.length-1))*(chartW-20)+10;
      const y = Math.min(chartH-2, Math.max(2, chartH - ((d.value-minVal)/range*chartH)));
      return {x,y};
    });
    const zeroY = Math.min(chartH-2, Math.max(2, chartH-((0-minVal)/range*chartH)));
    const areaPath = `M${pts[0].x},${zeroY} ${pts.map(p=>`L${p.x},${p.y}`).join(' ')} L${pts[pts.length-1].x},${zeroY} Z`;
    return <Path d={areaPath} fill={isPositive?'rgba(0,229,160,0.1)':'rgba(255,77,109,0.1)'}/>;
  })()}
  {/* Line */}
  {data.length > 1 && (()=>{
    const pts = data.map((d,i)=>{
      const x = (i/(data.length-1))*(chartW-20)+10;
      const y = Math.min(chartH-2, Math.max(2, chartH - ((d.value-minVal)/range*chartH)));
      return {x,y};
    });
    const pathD = `M${pts[0].x},${pts[0].y} ${pts.slice(1).map(p=>`L${p.x},${p.y}`).join(' ')}`;
    return <Path d={pathD} fill="none" stroke={isPositive?'#00e5a0':'#ff4d6d'} strokeWidth={2}/>;
  })()}
  {/* Dots */}
  {data.map((d,i)=>{
    const x = data.length > 1 ? (i/(data.length-1))*(chartW-20)+10 : chartW/2;
    const y = Math.min(chartH-2, Math.max(2, chartH - ((d.value-minVal)/range*chartH)));
    return <Circle key={i} cx={x} cy={y} r={3} fill={d.result==='Win'?'#00e5a0':d.result==='Loss'?'#ff4d6d':'#ffd166'}/>;
  })}
  {/* Y axis labels */}
  <SvgText x={2} y={8} fill="#4a6070" fontSize={9}>{maxVal>0?'+':''}{maxVal.toFixed(1)}{roiUnit==='units'?'u':'$'}</SvgText>
  <SvgText x={2} y={chartH} fill="#4a6070" fontSize={9}>{minVal.toFixed(1)}{roiUnit==='units'?'u':'$'}</SvgText>
</Svg>

                    {/* X axis labels */}
                    <View style={{flexDirection:'row',justifyContent:'space-between',paddingHorizontal:10,marginTop:2}}>
                      <Text style={{color:'#4a6070',fontSize:9}}>{data[0]?.date}</Text>
                      {data.length > 2 && <Text style={{color:'#4a6070',fontSize:9}}>{data[Math.floor(data.length/2)]?.date}</Text>}
                      <Text style={{color:'#4a6070',fontSize:9}}>{data[data.length-1]?.date}</Text>
                    </View>
                    {/* Stats row */}
                    <View style={{flexDirection:'row',justifyContent:'space-around',marginTop:12,paddingTop:12,borderTopWidth:1,borderTopColor:'#1f2d3d'}}>
                      {[
                        {label:'Best Bet',val:(()=>{const best=data.reduce((a,b)=>b.profit>a.profit?b:a,data[0]);return (best.profit>0?'+':'')+best.profit.toFixed(1)+(roiUnit==='units'?'u':'$');})(  ),color:'#00e5a0'},
                        {label:'Worst Bet',val:(()=>{const worst=data.reduce((a,b)=>b.profit<a.profit?b:a,data[0]);return (worst.profit>0?'+':'')+worst.profit.toFixed(1)+(roiUnit==='units'?'u':'$');})(  ),color:'#ff4d6d'},
                        {label:'Avg/Bet',val:(()=>{const avg=data.reduce((s,d)=>s+d.profit,0)/data.length;return (avg>0?'+':'')+avg.toFixed(1)+(roiUnit==='units'?'u':'$');})(  ),color:'#ffd166'},
                      ].map((s,i)=>(
                        <View key={i} style={{alignItems:'center'}}>
                          <Text style={{color:s.color,fontWeight:'800',fontSize:13}}>{s.val}</Text>
                          <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>{s.label}</Text>
                        </View>
                      ))}
                    </View>
                  </View>
                );
              })()}
              {roiChartTab==='sports'&&(()=>{
                const data = calcSportBreakdown(bets, roiUnit, unitSize);
                if(data.length === 0) return(
                  <View style={{alignItems:'center',paddingVertical:24}}>
                    <Text style={{fontSize:28}}>🏆</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,marginTop:8}}>Log settled bets to see sport breakdown</Text>
                  </View>
                );
                const maxUnits = Math.max(...data.map(d=>Math.abs(d.displayUnits)),0.1);
                return(
                  <View>
                    {data.map((s,i)=>(
                      <View key={i} style={{marginBottom:12}}>
                        <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:4}}>
                          <View style={{flexDirection:'row',alignItems:'center',gap:6}}>
                            <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13}}>{SPORT_EMOJI[s.sport]||'🎯'} {s.sport}</Text>
                            <Text style={{color:'#4a6070',fontSize:11}}>{s.wins}-{s.losses}{s.pushes>0?`-${s.pushes}`:''}</Text>
                          </View>
                          <View style={{alignItems:'flex-end'}}>
                            <Text style={{color:s.displayUnits>=0?'#00e5a0':'#ff4d6d',fontWeight:'800',fontSize:13}}>{s.displayUnits>=0?'+':''}{s.displayUnits.toFixed(1)}{roiUnit==='units'?'u':'$'}</Text>
                            <Text style={{color:'#4a6070',fontSize:10}}>{s.winRate}% WR</Text>
                          </View>
                        </View>
                        <View style={{flexDirection:'row',gap:4,height:8,borderRadius:4,overflow:'hidden',backgroundColor:'#1f2d3d'}}>
                          <View style={{flex:s.wins,backgroundColor:'#00e5a0',borderRadius:4}}/>
                          <View style={{flex:s.losses,backgroundColor:'#ff4d6d',borderRadius:4}}/>
                          {s.pushes>0&&<View style={{flex:s.pushes,backgroundColor:'#ffd166',borderRadius:4}}/>}
                        </View>
                        <View style={{height:6,backgroundColor:'#1f2d3d',borderRadius:3,overflow:'hidden',marginTop:4}}>
                          <View style={{height:'100%',width:`${(Math.abs(s.displayUnits)/maxUnits)*100}%`,backgroundColor:s.displayUnits>=0?'rgba(0,229,160,0.4)':'rgba(255,77,109,0.4)',borderRadius:3}}/>
                        </View>
                      </View>
                    ))}
                    <View style={{flexDirection:'row',gap:8,marginTop:4}}>
                      <View style={{flexDirection:'row',alignItems:'center',gap:4}}><View style={{width:8,height:8,borderRadius:4,backgroundColor:'#00e5a0'}}/><Text style={{color:'#4a6070',fontSize:10}}>Win</Text></View>
                      <View style={{flexDirection:'row',alignItems:'center',gap:4}}><View style={{width:8,height:8,borderRadius:4,backgroundColor:'#ff4d6d'}}/><Text style={{color:'#4a6070',fontSize:10}}>Loss</Text></View>
                      <View style={{flexDirection:'row',alignItems:'center',gap:4}}><View style={{width:8,height:8,borderRadius:4,backgroundColor:'#ffd166'}}/><Text style={{color:'#4a6070',fontSize:10}}>Push</Text></View>
                    </View>
                  </View>
                );
              })()}
            </View>
            <Text style={styles.sectionLabel}>RECENT PICKS</Text>
            {bets.slice(0,3).map(bet=>(
              <View key={bet.id} style={[styles.betCard,{borderLeftColor:resultColor(bet.result)}]}>
                <View style={styles.betTop}>
                  <View style={{flex:1}}><Text style={styles.betMatchup}>{bet.matchup}</Text><Text style={styles.betPick}>{bet.pick}</Text></View>
                  <TouchableOpacity style={[styles.pill,{backgroundColor:resultColor(bet.result)+'22'}]} onPress={()=>openEditModal(bet)}>
                    <Text style={{color:resultColor(bet.result),fontSize:11,fontWeight:'700'}}>{bet.result.toUpperCase()} ✏️</Text>
                  </TouchableOpacity>
                </View>
                <View style={styles.betMeta}>
                  <Text style={styles.metaChip}>🏆 {bet.sport}</Text>
                  <Text style={styles.metaChip}>{bet.odds}</Text>
                  <Text style={styles.metaChip}>{formatBetSize(bet.units)}</Text>
                  <Text style={[styles.metaChip,bet.book===HRB&&{borderColor:HRB_COLOR}]}><Text style={{color:bet.book===HRB?HRB_COLOR:'#7a92a8'}}>{bet.book===HRB?'🎸 ':''}{bet.book}</Text></Text>
                </View>
              </View>
            ))}
            {bets.length===0&&<View style={{alignItems:'center',paddingTop:40}}><Text style={{fontSize:32}}>🎯</Text><Text style={{color:'#7a92a8',marginTop:12,fontSize:14}}>No picks yet. Tap ➕ to log your first bet!</Text></View>}
          </View>
        )}

        {(activeTab==='picks'||(activeTab==='mybets'&&mybetsTab==='picks'))&&(
          <View>
            <View style={{flexDirection:'row',marginBottom:14,gap:0,backgroundColor:'#151c24',borderRadius:12,overflow:'hidden'}}>
              <TouchableOpacity style={{flex:1,paddingVertical:10,alignItems:'center',backgroundColor:mybetsTab==='picks'?'#1a2a3a':'transparent'}} onPress={()=>setMybetsTab('picks')}>
                <Text style={{color:mybetsTab==='picks'?'#00e5a0':'#7a92a8',fontWeight:'700',fontSize:13}}>My Picks</Text>
              </TouchableOpacity>
              <TouchableOpacity style={{flex:1,paddingVertical:10,alignItems:'center',backgroundColor:mybetsTab==='parlay_sub'?'#1a2a3a':'transparent'}} onPress={()=>setMybetsTab('parlay_sub')}>
                <Text style={{color:mybetsTab==='parlay_sub'?'#00e5a0':'#7a92a8',fontWeight:'700',fontSize:13}}>Parlay Builder</Text>
              </TouchableOpacity>
            </View>
            {mybetsTab==='picks'&&(
            <View>
            <Text style={styles.pageTitle}>My Picks</Text>
            <View style={styles.statRow}>
              <View style={[styles.statBox,styles.statGreen]}><Text style={[styles.statVal,{color:'#00e5a0'}]}>{wins}W</Text><Text style={styles.statKey}>Wins</Text></View>
              <View style={[styles.statBox,styles.statRed]}><Text style={[styles.statVal,{color:'#ff4d6d'}]}>{losses}L</Text><Text style={styles.statKey}>Losses</Text></View>
              <View style={[styles.statBox,styles.statBlue]}><Text style={[styles.statVal,{color:'#0099ff',fontSize:trackingMode==='dollars'?15:20}]}>{trackingMode==='units'?(totalUnits>=0?'+':'')+totalUnits.toFixed(1)+'u':(totalDollars>=0?'+':'-')+'$'+Math.abs(totalDollars).toFixed(0)}</Text><Text style={styles.statKey}>{trackingMode==='units'?'UNITS':'Profit'}</Text></View>
            </View>
            <Text style={styles.sectionLabel}>ALL BETS — {bets.length} total</Text>
            {bets.map(bet=>(
              <View key={bet.id} style={[styles.betCard,{borderLeftColor:resultColor(bet.result)}]}>
                <View style={styles.betTop}>
                  <View style={{flex:1}}><Text style={styles.betMatchup}>{bet.matchup}</Text><Text style={styles.betPick}>{bet.pick} • {bet.type}</Text></View>
                  <TouchableOpacity style={[styles.pill,{backgroundColor:resultColor(bet.result)+'22'}]} onPress={()=>openEditModal(bet)}>
                    <Text style={{color:resultColor(bet.result),fontSize:11,fontWeight:'700'}}>{bet.result.toUpperCase()} ✏️</Text>
                  </TouchableOpacity>
                </View>
                {bet.result==='Pending'&&(
                  <View style={{flexDirection:'row',gap:6,marginTop:8}}>
                    <TouchableOpacity style={styles.quickBtn} onPress={()=>quickUpdateResult(bet.id,'Win')}><Text style={{color:'#00e5a0',fontSize:12,fontWeight:'700'}}>✓ Win</Text></TouchableOpacity>
                    <TouchableOpacity style={styles.quickBtn} onPress={()=>quickUpdateResult(bet.id,'Loss')}><Text style={{color:'#ff4d6d',fontSize:12,fontWeight:'700'}}>✗ Loss</Text></TouchableOpacity>
                    <TouchableOpacity style={styles.quickBtn} onPress={()=>quickUpdateResult(bet.id,'Push')}><Text style={{color:'#ffd166',fontSize:12,fontWeight:'700'}}>~ Push</Text></TouchableOpacity>
                  </View>
                )}
                <View style={styles.betMeta}>
                  <Text style={styles.metaChip}>🏆 {bet.sport}</Text>
                  <Text style={styles.metaChip}>{bet.odds}</Text>
                  <Text style={styles.metaChip}>{formatBetSize(bet.units)}</Text>
                  <Text style={[styles.metaChip,bet.book===HRB&&{borderColor:HRB_COLOR}]}><Text style={{color:bet.book===HRB?HRB_COLOR:'#7a92a8'}}>{bet.book===HRB?'🎸 ':''}{bet.book}</Text></Text>
                  {bet.date&&<Text style={styles.metaChip}>📅 {bet.date}</Text>}
                  <TouchableOpacity onPress={()=>deleteBet(bet.id)}><Text style={{color:'#4a6070',fontSize:11,paddingVertical:2}}>🗑</Text></TouchableOpacity>
                </View>
              </View>
            ))}
            {bets.length===0&&<View style={{alignItems:'center',paddingTop:40}}><Text style={{fontSize:32}}>🎯</Text><Text style={{color:'#7a92a8',marginTop:12,fontSize:14}}>No picks logged yet.</Text></View>}
            <TouchableOpacity style={styles.btnPrimary} onPress={()=>setModalVisible(true)}><Text style={styles.btnPrimaryText}>+ Log New Pick</Text></TouchableOpacity>
            <View style={{height:20}}/>
            </View>
            )}
          </View>
        )}

        {activeTab==='games'&&(
          <View>
            <Text style={styles.pageTitle}>Games</Text>
            <View style={{flexDirection:'row',gap:6,marginBottom:14}}>
              {['yesterday','today','tomorrow'].map(d=>(
                <TouchableOpacity key={d} style={[styles.chipBtn,gamesDay===d&&styles.chipBtnActive,{flex:1,alignItems:'center'}]} onPress={()=>setGamesDay(d)}>
                  <Text style={[styles.chipTxt,gamesDay===d&&styles.chipTxtActive]}>{d.charAt(0).toUpperCase()+d.slice(1)}</Text>
                </TouchableOpacity>
              ))}
            </View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:14}}>
              <View style={{flexDirection:'row',gap:6}}>
                {SPORTS.map(s=>(<TouchableOpacity key={s} style={[styles.chipBtn,gamesSport===s&&styles.chipBtnActive]} onPress={()=>setGamesSport(s)}><Text style={[styles.chipTxt,gamesSport===s&&styles.chipTxtActive]}>{SPORT_EMOJI[s]} {s}</Text></TouchableOpacity>))}
              </View>
            </ScrollView>
            <View style={{flexDirection:'row',alignItems:'center',backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',borderRadius:12,paddingHorizontal:12,marginBottom:14}}>
  <Text style={{fontSize:16,marginRight:8}}>🔍</Text>
  <TextInput
    style={{flex:1,color:'#e8f0f8',fontSize:14,paddingVertical:10}}
    placeholder="Search teams..."
    placeholderTextColor="#4a6070"
    value={gamesSearch}
    onChangeText={setGamesSearch}
    returnKeyType="search"
  />
  {gamesSearch.length>0&&(
    <TouchableOpacity onPress={()=>setGamesSearch('')}>
      <Text style={{color:'#4a6070',fontSize:16}}>✕</Text>
    </TouchableOpacity>
  )}
</View>
<View style={{flexDirection:'row',gap:6,marginBottom:14}}>
  {[{id:'time',label:'⏰ Time'},{id:'score',label:'🔥 Sweat Score'},{id:'hrb',label:'🎸 HRB First'}].map(s=>(
    <TouchableOpacity key={s.id}
      style={[styles.chipBtn,gamesSort===s.id&&styles.chipBtnActive,{flex:1,justifyContent:'center',alignItems:'center'}]}
      onPress={()=>setGamesSort(s.id)}>
      <Text style={[styles.chipTxt,gamesSort===s.id&&styles.chipTxtActive,{textAlign:'center'}]}>{s.label}</Text>
    </TouchableOpacity>
  ))}
</View>

           {gamesSport==='MLB'&&(
  <View style={{backgroundColor:'rgba(0,153,255,0.06)',borderRadius:12,padding:12,marginBottom:14,borderWidth:1,borderColor:'rgba(0,153,255,0.2)'}}>
    <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:6}}>
      <Text style={{color:'#0099ff',fontWeight:'800',fontSize:12}}>⚾ MLB MODEL ACTIVE</Text>
      <Text style={{color:'#4a6070',fontSize:10}}>🔄 8am + 2pm ET</Text>
    </View>
    <Text style={{color:'#7a92a8',fontSize:11,lineHeight:16}}>Pipeline updates twice daily. Lineups confirm 2-3hrs before first pitch. Umpires post overnight. Check back at 2pm for full confirmed slate.</Text>
  </View>
)}
{gamesSport==='MLB' && hrWatch.filter((h:any) => {
  const mg = gamesData.find((g:any) => g.home_team === h.homeTeam || g.home_team?.includes(h.homeTeam?.split(' ').pop()));
  if(!mg) return false;
  return new Date(mg.commence_time) > new Date();
}).length > 0 && (
  <View style={{backgroundColor:'rgba(255,77,109,0.06)',borderRadius:14,padding:14,marginBottom:14,borderWidth:1,borderColor:'rgba(255,77,109,0.25)'}}>
    <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
      <Text style={{color:'#ff4d6d',fontWeight:'800',fontSize:13}}>💣 HR WATCH</Text>
      <Text style={{color:'#4a6070',fontSize:10}}>Power + Pitcher + Environment</Text>
    </View>
    {hrWatch.filter((h:any) => {
      const mg = gamesData.find((g:any) => g.home_team === h.homeTeam);
      return !mg || new Date(mg.commence_time) > new Date();
    }).map((h: any, i: number) => (
      <View key={i} style={{flexDirection:'row',alignItems:'center',paddingVertical:8,borderTopWidth:i>0?1:0,borderTopColor:'#1f2d3d'}}>
        <View style={{flex:1}}>
          <View style={{flexDirection:'row',alignItems:'center',gap:6}}>
            <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13}}>{h.player}</Text>
            {h.isFallback && <Text style={{color:'#7a92a8',fontSize:9,fontStyle:'italic'}}>est. lineup</Text>}
          </View>
          <Text style={{color:'#7a92a8',fontSize:10,marginTop:2}}>{h.game}</Text>
        </View>
        <View style={{alignItems:'flex-end',gap:3}}>
          <View style={{flexDirection:'row',gap:4,flexWrap:'wrap',justifyContent:'flex-end'}}>
            <View style={{backgroundColor:'rgba(255,77,109,0.15)',borderRadius:5,paddingHorizontal:5,paddingVertical:1}}>
              <Text style={{color:'#ff4d6d',fontSize:9,fontWeight:'700'}}>{h.hr} HR / {h.pa} PA</Text>
            </View>
            {h.parkFactor >= 105 && <View style={{backgroundColor:'rgba(255,184,0,0.12)',borderRadius:5,paddingHorizontal:5,paddingVertical:1}}>
              <Text style={{color:HRB_COLOR,fontSize:9,fontWeight:'700'}}>Park {h.parkFactor}</Text>
            </View>}
            {h.windOut && <View style={{backgroundColor:'rgba(0,229,160,0.12)',borderRadius:5,paddingHorizontal:5,paddingVertical:1}}>
              <Text style={{color:'#00e5a0',fontSize:9,fontWeight:'700'}}>Wind Out {h.windSpeed}mph</Text>
            </View>}
            {h.oppXera && h.oppXera > 4.0 && <View style={{backgroundColor:'rgba(0,153,255,0.12)',borderRadius:5,paddingHorizontal:5,paddingVertical:1}}>
              <Text style={{color:'#0099ff',fontSize:9,fontWeight:'700'}}>vs {h.oppXera.toFixed(1)} xERA</Text>
            </View>}
          </View>
          <Text style={{color:'#4a6070',fontSize:9}}>vs {h.oppPitcher?.split(' ').pop() || 'TBD'} • {h.venue?.split(' ').pop() || ''} • {h.temp}°F</Text>
        </View>
      </View>
    ))}
  </View>
)}
{gamesSport==='MLB' && (()=>{
  const now = new Date();
  // Dedupe by game_id (mlbGameContext keyed by both home and away team, so each game appears twice)
  const seenGames = new Set<string>();
  const mlLeans = Object.values(mlbGameContext as Record<string, any>)
    .filter((ctx:any) => {
      if(!ctx.game_id || seenGames.has(ctx.game_id)) return false;
      if(!ctx.spread_delta || Math.abs(parseFloat(ctx.spread_delta)) < 3.0) return false;
      const mg = gamesData.find((g:any) => g.home_team === ctx.home_team || g.away_team === ctx.away_team);
      if(!mg) return false;
      if(new Date(mg.commence_time) <= now) return false;
      seenGames.add(ctx.game_id);
      return true;
    })
    .sort((a:any, b:any) => Math.abs(parseFloat(b.spread_delta)) - Math.abs(parseFloat(a.spread_delta)))
    .slice(0, 5);
  if(mlLeans.length === 0) return null;
  return (
    <View style={{backgroundColor:'rgba(0,229,160,0.06)',borderRadius:14,padding:14,marginBottom:14,borderWidth:1,borderColor:'rgba(0,229,160,0.25)'}}>
      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
        <Text style={{color:'#00e5a0',fontWeight:'800',fontSize:13}}>💰 ML LEANS</Text>
        <Text style={{color:'#4a6070',fontSize:10}}>Model disagrees with market</Text>
      </View>
      {mlLeans.map((ctx:any, i:number) => {
        const delta = parseFloat(ctx.spread_delta);
        const absDelta = Math.abs(delta);
        const favTeam = delta > 0 ? ctx.home_team : ctx.away_team;
        const tierColor = absDelta >= 5 ? '#00e5a0' : absDelta >= 4 ? '#00e5a0' : '#4a9eff';
        const tierLabel = absDelta >= 5 ? 'ELITE' : absDelta >= 4 ? 'PRIME' : 'LEAN';
        return (
          <View key={i} style={{flexDirection:'row',alignItems:'center',paddingVertical:8,borderTopWidth:i>0?1:0,borderTopColor:'#1f2d3d'}}>
            <View style={{flex:1}}>
              <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13}}>{favTeam.split(' ').pop()} ML</Text>
              <Text style={{color:'#7a92a8',fontSize:10,marginTop:2}}>{ctx.away_team.split(' ').pop()} @ {ctx.home_team.split(' ').pop()}</Text>
            </View>
            <View style={{alignItems:'flex-end',gap:3}}>
              <View style={{backgroundColor:tierColor+'20',borderRadius:6,paddingHorizontal:6,paddingVertical:2,borderWidth:1,borderColor:tierColor+'44'}}>
                <Text style={{color:tierColor,fontWeight:'800',fontSize:10}}>{tierLabel} {delta > 0 ? '+' : ''}{delta.toFixed(1)}</Text>
              </View>
              <Text style={{color:'#4a6070',fontSize:9}}>vs market</Text>
            </View>
          </View>
        );
      })}
    </View>
  );
})()}
            {gamesLoading?(<View style={{alignItems:'center',paddingTop:60}}><ActivityIndicator size="large" color={HRB_COLOR}/><Text style={{color:'#7a92a8',marginTop:12}}>Loading games...</Text></View>):
            gamesData.length===0?(<View style={{alignItems:'center',paddingTop:60}}><Text style={{fontSize:40}}>{SPORT_EMOJI[gamesSport]}</Text><Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>No {gamesSport} games {gamesDay}.{'\n'}Try a different sport or day.</Text></View>):(
              <>
                {gamesSport==='NHL' && (
                  <View style={{backgroundColor:'rgba(255,184,0,0.08)',borderRadius:10,padding:10,marginBottom:12,borderWidth:1,borderColor:'rgba(255,184,0,0.25)',flexDirection:'row',alignItems:'center',gap:8}}>
                    <Text style={{fontSize:14}}>ℹ️</Text>
                    <Text style={{color:'#b0c4d8',fontSize:11,flex:1,lineHeight:16}}>
                      <Text style={{color:HRB_COLOR,fontWeight:'700'}}>NHL — Market Model Only.</Text> No proprietary NHL pipeline yet. Analysis based on odds movement, public consensus, and goalie matchup. Proprietary model coming later this season.
                    </Text>
                  </View>
                )}
                 <Text style={styles.sectionLabel}>{gamesData.length} GAMES — {gamesDay.toUpperCase()}</Text>
                {gamesData.filter((game) => {
  // Hide completed games
  if(game.gameState === 'Final') return false;
  const gameTime = new Date(game.commence_time);
  const fourHoursAgo = new Date(Date.now() - 4*60*60*1000);
  if(gameTime < fourHoursAgo) return false;
  // Search filter
  if(gamesSearch === '') return true;
  return game.away_team.toLowerCase().includes(gamesSearch.toLowerCase()) ||
    game.home_team.toLowerCase().includes(gamesSearch.toLowerCase());
}).sort((a, b) => {
  if(gamesSort === 'time') return new Date(a.commence_time) - new Date(b.commence_time);
  if(gamesSort === 'score') {
    const scoreA = sweatScores[a.id]?.total || getSweatScoreForGame(a, gamesSport)?.total || 0;
    const scoreB = sweatScores[b.id]?.total || getSweatScoreForGame(b, gamesSport)?.total || 0;
    return scoreB - scoreA;
  }
  if(gamesSort === 'hrb') {
    const aHasHRB = (a.bookmakers||[]).some(bm => bm.key==='hardrockbet'||bm.key==='hardrock');
    const bHasHRB = (b.bookmakers||[]).some(bm => bm.key==='hardrockbet'||bm.key==='hardrock');
    if(aHasHRB && !bHasHRB) return -1;
    if(!aHasHRB && bHasHRB) return 1;
    return new Date(a.commence_time) - new Date(b.commence_time);
  }
  return 0;
}).map((game, i) => {
                  const summary=getGameSummary(game);
                  const gameTime=new Date(game.commence_time);
                  const isLive=game.gameState==='Live'||(new Date()>gameTime&&new Date()<new Date(gameTime.getTime()+4*60*60*1000));
                  //console.log('HRB search - bookmaker keys:', game.bookmakers.map(bm=>bm.key));
                  const hrbLine=getHRBLine(game);
                  const hrbSpread=hrbLine&&hrbLine.spread?hrbLine.spread[0]:null;
                  const hrbTotal=hrbLine&&hrbLine.total?hrbLine.total[0]:null;
                  return(
                    <TouchableOpacity key={i} style={styles.gameCard} onPress={()=>openGameDetail(game)} activeOpacity={0.8}>
                      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
                        <Text style={{fontSize:11,color:'#7a92a8',fontWeight:'600'}}>{gameTime.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',timeZone:'America/New_York'})} ET</Text>
                        {isLive?(<View style={{flexDirection:'row',alignItems:'center',gap:4,backgroundColor:'rgba(255,77,109,0.15)',paddingHorizontal:8,paddingVertical:3,borderRadius:20}}><View style={{width:6,height:6,borderRadius:3,backgroundColor:'#ff4d6d'}}/><Text style={{color:'#ff4d6d',fontSize:11,fontWeight:'700'}}>LIVE</Text></View>):
                        (<View style={[styles.pill,{backgroundColor:'rgba(0,153,255,0.15)'}]}><Text style={{color:'#0099ff',fontSize:11,fontWeight:'700'}}>{gamesSport}</Text></View>)}
                      </View>
                       {(()=>{
                          if(gamesSport==='NCAAB' && !bartData.length) return null;
  
                  const ss = getSweatScoreForGame(game, gamesSport);
                  if(!ss) return null;
                        const tier = getSweatTier(ss.total);
                        // Build top signals for display
                        const signals = [];
                        if(gamesSport === 'MLB') {
                          const mlbCtx = mlbGameContext[game.home_team] || mlbGameContext[game.away_team] ||
                            mlbGameContext[game.home_team?.trim()] || mlbGameContext[game.away_team?.trim()] ||
                            Object.values(mlbGameContext).find((ctx: any) =>
                              ctx.home_team === game.home_team || ctx.away_team === game.away_team ||
                              ctx.home_team === game.away_team || ctx.away_team === game.home_team
                            );
                          if(mlbCtx) {
                            if(mlbCtx.projected_total && mlbCtx.projected_total > 0) {
                              const totals = (game.bookmakers||[]).map(bm => {
                                const t = bm.markets && bm.markets.find(m => m.key==='totals');
                                return t && t.outcomes && t.outcomes[0] ? parseFloat(t.outcomes[0].point) : null;
                              }).filter(x => x !== null);
                              const avgTotal = totals.length ? totals.reduce((a,b)=>a+b,0)/totals.length : null;
                              if(avgTotal) {
                                const delta = (mlbCtx.projected_total - avgTotal).toFixed(1);
                                if(Math.abs(parseFloat(delta)) >= 0.5) signals.push(`Model: ${parseFloat(delta) < 0 ? '⬇️' : '⬆️'} ${Math.abs(parseFloat(delta))}R ${parseFloat(delta) < 0 ? 'under' : 'over'} lean`);
                              }
                            }
                            if(mlbCtx.home_k_gap && Math.abs(mlbCtx.home_k_gap) >= 4) signals.push(`K gap: ${mlbCtx.home_k_gap > 0 ? '+' : ''}${mlbCtx.home_k_gap}pts`);
                            if(mlbCtx.away_k_gap && Math.abs(mlbCtx.away_k_gap) >= 4 && signals.length < 2) signals.push(`K gap: ${mlbCtx.away_k_gap > 0 ? '+' : ''}${mlbCtx.away_k_gap}pts`);
                            if(mlbCtx.home_platoon_note && signals.length < 2) signals.push(mlbCtx.home_platoon_note.split('—')[1]?.trim() || 'Platoon edge');
                            if(mlbCtx.temperature <= 45 && signals.length < 3) signals.push(`❄️ ${mlbCtx.temperature}°F`);
                            if(mlbCtx.park_run_factor >= 110 && signals.length < 3) signals.push(`🏟️ Hitter park ${mlbCtx.park_run_factor}`);
                            if(mlbCtx.park_run_factor <= 93 && signals.length < 3) signals.push(`🏟️ Pitcher park ${mlbCtx.park_run_factor}`);
                          }
                        } else if(gamesSport === 'NBA') {
                          const homeNBA = nbaTeamData[game.home_team] || Object.values(nbaTeamData).find(t => t.team && game.home_team.includes(t.team.split(' ').pop()));
                          const awayNBA = nbaTeamData[game.away_team] || Object.values(nbaTeamData).find(t => t.team && game.away_team.includes(t.team.split(' ').pop()));
                          if(homeNBA && awayNBA) {
                            const netGap = Math.abs(homeNBA.net_rating - awayNBA.net_rating);
                            if(netGap >= 3) signals.push(`Net rtg gap: ${netGap.toFixed(1)}pts`);
                            if(homeNBA.injury_note?.includes('OUT')) signals.push(`⚠️ ${game.home_team.split(' ').pop()} injuries`);
                            if(awayNBA.injury_note?.includes('OUT')) signals.push(`⚠️ ${game.away_team.split(' ').pop()} injuries`);
                            const homeWinPct = homeNBA.home_wins/(homeNBA.home_wins+homeNBA.home_losses||1);
                            const awayWinPct = awayNBA.away_wins/(awayNBA.away_wins+awayNBA.away_losses||1);
                            if(homeWinPct - awayWinPct >= 0.2 && signals.length < 2) signals.push(`${game.home_team.split(' ').pop()} ${homeNBA.home_record} home`);
                          }
                        } else if(gamesSport === 'NCAAB' && ss.efgMismatch) {
                          const topMismatch = ss.efgMismatch.split('|')[0]?.trim();
                          if(topMismatch) signals.push(topMismatch);
                          if(ss.mismatchPts && Math.abs(ss.mismatchPts) >= 3) signals.push(`Model edge: ${ss.mismatchPts > 0 ? '+' : ''}${ss.mismatchPts}pts`);
                        }
                        if(ss.leanSide && signals.length < 3) signals.push(`Lean: ${ss.leanSide}`);

                        return(
  <View style={{marginBottom:8}}>
    <View style={{flexDirection:'row',alignItems:'center',gap:6,marginBottom:signals.length > 0 ? 5 : 0}}>
      {isLive ? (
        <View style={{paddingHorizontal:10,paddingVertical:4,borderRadius:20,backgroundColor:'#ff4d6d22',borderWidth:1,borderColor:'#ff4d6d',flexDirection:'row',alignItems:'center',gap:4}}>
          <View style={{width:6,height:6,borderRadius:3,backgroundColor:'#ff4d6d'}}/>
          <Text style={{color:'#ff4d6d',fontWeight:'800',fontSize:11}}>LIVE</Text>
        </View>
      ) : (
        <View style={{paddingHorizontal:10,paddingVertical:4,borderRadius:20,backgroundColor:tier.color+'22',borderWidth:1,borderColor:tier.color,flexDirection:'row',alignItems:'center',gap:4}}>
          <Text style={{color:tier.color,fontWeight:'800',fontSize:13}}>{ss.total}</Text>
          <Text style={{color:tier.color,fontSize:10,fontWeight:'700'}}>SWEAT</Text>
        </View>
      )}
      <Text style={{color:isLive ? '#ff4d6d' : tier.color,fontSize:11,fontWeight:'600'}}>{isLive ? 'In Progress' : tier.label}</Text>
    </View>
                            {signals.length > 0 && (
                              <View style={{flexDirection:'row',flexWrap:'wrap',gap:4}}>
                                {signals.slice(0,3).map((sig,i) => (
                                  <View key={i} style={{backgroundColor:'rgba(255,255,255,0.05)',borderRadius:6,paddingHorizontal:6,paddingVertical:2,borderWidth:1,borderColor:'#1f2d3d'}}>
                                    <Text style={{color:'#7a92a8',fontSize:9,fontWeight:'600'}}>{sig}</Text>
                                  </View>
                                ))}
                              </View>
                            )}
                          </View>
                        );
                      })()}
                       {(()=>{
                        //console.log('Badge check - bartData:', bartData.length, 'away:', stripMascot(game.away_team), 'match:', fuzzyMatchTeam(stripMascot(game.away_team), bartData, 'team')?.team);
                        const awayKP = gamesSport==='NCAAB' ? fuzzyMatchTeam(stripMascot(game.away_team), bartData, 'team') : null;
                        const homeKP = gamesSport==='NCAAB' ? fuzzyMatchTeam(stripMascot(game.home_team), bartData, 'team') : null;
                        return(
                          <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
                            <View style={{flex:1}}>
                              <Text style={{fontSize:15,fontWeight:'700',color:'#e8f0f8'}}>{stripMascot(game.away_team)}</Text>
                              {awayKP&&<View style={{flexDirection:'row',alignItems:'center',gap:4,marginTop:3}}>
                                <Text style={{fontSize:10,color:'#00e5a0',fontWeight:'700'}}>#{awayKP.adjOERank} OFF</Text>
                                <Text style={{fontSize:10,color:'#0099ff',fontWeight:'700'}}>#{awayKP.adjDERank} DEF</Text>
                              </View>}
                              {!awayKP&&<Text style={{fontSize:11,color:'#7a92a8',marginTop:2}}>Away</Text>}
                            </View>
                            <View style={{paddingHorizontal:12,paddingVertical:6,backgroundColor:'#151c24',borderRadius:8,borderWidth:1,borderColor:'#1f2d3d'}}>
                              <Text style={{color:'#4a6070',fontWeight:'800',fontSize:12}}>@</Text>
                            </View>
                            <View style={{flex:1,alignItems:'flex-end'}}>
                              <Text style={{fontSize:15,fontWeight:'700',color:'#e8f0f8'}}>{stripMascot(game.home_team)}</Text>
                              {homeKP&&<View style={{flexDirection:'row',alignItems:'center',gap:4,marginTop:3,justifyContent:'flex-end'}}>
                                <Text style={{fontSize:10,color:'#00e5a0',fontWeight:'700'}}>#{homeKP.adjOERank} OFF</Text>
                                <Text style={{fontSize:10,color:'#0099ff',fontWeight:'700'}}>#{homeKP.adjDERank} DEF</Text>
                              </View>}
                              {!homeKP&&<Text style={{fontSize:11,color:'#7a92a8',marginTop:2}}>Home</Text>}
                            </View>
                          </View>
                        );
                      })()}
{gamesSport === 'NBA' && isPlayoffMode && (()=>{
  const series = playoffSeries[game.home_team] || playoffSeries[game.away_team];
  if(!series) return null;
  return(
    <View style={{backgroundColor:'rgba(255,184,0,0.1)',borderRadius:8,paddingHorizontal:10,paddingVertical:4,marginBottom:8,borderWidth:1,borderColor:'rgba(255,184,0,0.3)',alignSelf:'flex-start'}}>
      <Text style={{color:'#FFB800',fontSize:11,fontWeight:'800'}}>🏆 {series.series_label} — Game {series.game_number}</Text>
    </View>
  );
})()}
{gamesSport==='MLB'&&(()=>{
  const nrfiCtx = mlbGameContext[game.home_team] || mlbGameContext[game.away_team] ||
    Object.values(mlbGameContext).find((ctx: any) => ctx.home_team === game.home_team || ctx.away_team === game.away_team);
  if(!nrfiCtx) return null;
  const nScore = nrfiCtx.nrfi_score;
  const pf = nrfiCtx.park_run_factor ? parseFloat(nrfiCtx.park_run_factor) : 100;
  const spreadDelta = nrfiCtx.spread_delta != null ? parseFloat(nrfiCtx.spread_delta) : null;
  // Show badge only for tiers with proven edge (audit-backed): 90+ NRFI, 70-79 mild lean, <=40 YRFI
  // 80-89 tier is 42.5% hit rate — hide | Coors-type parks (116+) = 12.5% NRFI — suppress NRFI badge
  const suppressNrfiAtExtremePark = pf >= 116 && nScore >= 70;
  const hasNrfiBadge = !suppressNrfiAtExtremePark && nScore && (nScore >= 90 || (nScore >= 70 && nScore <= 79) || nScore <= 40);
  const hasMlBadge = spreadDelta != null && Math.abs(spreadDelta) >= 3.0;
  if(!hasNrfiBadge && !hasMlBadge) return null;

  const nColor = nScore >= 90 && nScore <= 94 ? '#00e5a0' : nScore >= 95 ? '#ffb800' : nScore >= 70 && nScore <= 79 ? '#4a9eff' : nScore <= 40 ? '#ff4d6d' : '#7a92a8';
  // Tier labels based on 235-game audit:
  // 90-94: 73.3% (PRIME), 95+: 47% (volatile warning), 75-79: 60.9%, 70-74: 59.4%
  // 85-89: 47.1% (too weak for badge), 80-84: 38.9% (no badge)
  // <=40: 77.8% YRFI hit rate
  const nLabel = nScore >= 95 ? 'NRFI ⚠️' : nScore >= 90 ? 'PRIME NRFI' : nScore >= 70 && nScore <= 79 ? 'NRFI lean' : nScore <= 35 ? 'YRFI' : nScore <= 40 ? 'YRFI lean' : null;

  // ML lean badge — 3+ spread delta is 70% historical, 5+ is 100%
  const mlTeam = spreadDelta != null ? (spreadDelta > 0 ? nrfiCtx.home_team : nrfiCtx.away_team) : null;
  const mlColor = Math.abs(spreadDelta || 0) >= 5 ? '#00e5a0' : Math.abs(spreadDelta || 0) >= 4 ? '#00e5a0' : '#4a9eff';
  const mlLabel = Math.abs(spreadDelta || 0) >= 5 ? 'ELITE ML' : Math.abs(spreadDelta || 0) >= 4 ? 'PRIME ML' : 'ML LEAN';

  return(
    <View style={{flexDirection:'row',alignItems:'center',gap:6,marginBottom:8,flexWrap:'wrap'}}>
      {hasNrfiBadge && nLabel && (
        <View style={{backgroundColor:nColor+'20',borderRadius:8,paddingHorizontal:8,paddingVertical:4,borderWidth:1,borderColor:nColor+'44',flexDirection:'row',alignItems:'center',gap:4}}>
          <Text style={{color:nColor,fontWeight:'800',fontSize:12}}>⚾ {nLabel}</Text>
          <Text style={{color:nColor,fontWeight:'800',fontSize:12}}>{nScore}</Text>
        </View>
      )}
      {hasMlBadge && mlTeam && (
        <View style={{backgroundColor:mlColor+'20',borderRadius:8,paddingHorizontal:8,paddingVertical:4,borderWidth:1,borderColor:mlColor+'44',flexDirection:'row',alignItems:'center',gap:4}}>
          <Text style={{color:mlColor,fontWeight:'800',fontSize:12}}>💰 {mlLabel}</Text>
          <Text style={{color:mlColor,fontWeight:'800',fontSize:12}}>{mlTeam.split(' ').pop()} {spreadDelta > 0 ? '+' : ''}{spreadDelta.toFixed(1)}</Text>
        </View>
      )}
      {nrfiCtx.home_pitcher && <Text style={{color:'#4a6070',fontSize:10}}>{nrfiCtx.home_pitcher?.split(' ').pop()} vs {nrfiCtx.away_pitcher?.split(' ').pop()}</Text>}
    </View>
  );
})()}
                      {hrbLine?(
                        <View style={{backgroundColor:'rgba(255,184,0,0.07)',borderRadius:10,padding:10,marginBottom:8,borderWidth:1,borderColor:'rgba(255,184,0,0.25)'}}>
                          <Text style={{color:HRB_COLOR,fontSize:10,fontWeight:'800',marginBottom:6}}>🎸 HARD ROCK BET</Text>
                          <View style={{flexDirection:'row',gap:6}}>
                            {hrbSpread&&gamesSport!=='UFC'&&<View style={{flex:1,alignItems:'center'}}><Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>SPREAD</Text><Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:13,marginTop:2}}>{hrbSpread.name.split(' ').pop()} {hrbSpread.point>0?'+':''}{hrbSpread.point}</Text><Text style={{color:'#7a92a8',fontSize:10}}>{hrbSpread.price>0?'+':''}{hrbSpread.price}</Text></View>}
                            {hrbTotal&&<View style={{flex:1,alignItems:'center'}}><Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>TOTAL</Text><Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:13,marginTop:2}}>O/U {hrbTotal.point}</Text><Text style={{color:'#7a92a8',fontSize:10}}>{hrbTotal.price>0?'+':''}{hrbTotal.price}</Text></View>}
                            {hrbLine.ml&&hrbLine.ml[0]&&<View style={{flex:1,alignItems:'center'}}><Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>ML</Text><Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:13,marginTop:2}}>{hrbLine.ml[0].price>0?'+':''}{hrbLine.ml[0].price}</Text><Text style={{color:'#7a92a8',fontSize:10}}>{game.away_team.split(' ').pop()}</Text></View>}
                          </View>
                        </View>
                      ):(
                        <View style={{flexDirection:'row',gap:6,marginBottom:8}}>
                          {gamesSport!=='UFC'&&(<View style={styles.oddsQuickChip}><Text style={styles.oddsQuickLabel}>SPREAD</Text><Text style={styles.oddsQuickVal}>{summary.spread}</Text></View>)}
                          <View style={styles.oddsQuickChip}><Text style={styles.oddsQuickLabel}>TOTAL</Text><Text style={styles.oddsQuickVal}>{summary.total}</Text></View>
                          <View style={styles.oddsQuickChip}><Text style={styles.oddsQuickLabel}>ML</Text><Text style={styles.oddsQuickVal}>{summary.mlAway}/{summary.mlHome}</Text></View>
                        </View>
                      )}
                      <Text style={{color:'#4a6070',fontSize:11,textAlign:'right'}}>Tap for full matchup →</Text>
                    </TouchableOpacity>
                  );
                })}
              </>
            )}
            <View style={{height:20}}/>
          </View>
        )}

        {(activeTab==='trends'||activeTab==='jerry')&&(
          <View>
            <Text style={styles.pageTitle}>🧠 Jerry 🎤</Text>
            <View style={{flexDirection:'row',gap:6,marginBottom:14}}>
                {[{id:'propjerry',label:'🧠 Prop Jerry'},{id:'dailydegen',label:'🎲 Daily Degen'},{id:'fades',label:'🚫 Fades'},{id:'mytrends',label:'📋 Record'}].map(t=>(
                  <TouchableOpacity key={t.id} style={[styles.chipBtn,{flex:1,justifyContent:'center',alignItems:'center'},trendsTab===t.id&&styles.chipBtnActive]} onPress={()=>setTrendsTab(t.id)}>
                    <Text style={[styles.chipTxt,trendsTab===t.id&&styles.chipTxtActive,{textAlign:'center'}]}>{t.label}</Text>
                  </TouchableOpacity>
                ))}
            </View>


             {trendsTab==='propjerry'&&(
  <View>
    <View style={{backgroundColor:'rgba(255,184,0,0.07)',borderRadius:12,padding:12,marginBottom:14,borderWidth:1,borderColor:'rgba(255,184,0,0.25)'}}>
      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start'}}>
  <View style={{flex:1}}>
    <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14,marginBottom:4}}>🎤 PROP JERRY</Text>
    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Pure EV + market consensus. No simulated data. Jerry finds the real edges.</Text>
  </View>
  <TouchableOpacity onPress={async()=>{const lastRefresh=await AsyncStorage.getItem('propjerry_last_refresh');if(lastRefresh&&Date.now()-parseInt(lastRefresh)<5*60*1000){showToast('⏳ Wait 5 minutes between refreshes');return;}await AsyncStorage.setItem('propjerry_last_refresh',String(Date.now()));try{await AsyncStorage.removeItem(PROP_JERRY_CACHE_KEY+'_'+propJerrySport);}catch(e){}try{await supabase.from('prop_jerry_cache').delete().eq('sport',propJerrySport);}catch(e){}fetchPropJerry(propJerrySport);}} style={{alignItems:'center',gap:3}}>
    <Text style={{fontSize:18}}>🔄</Text>
    <Text style={{color:'#4a6070',fontSize:9}}>{propJerryLastUpdate ? Math.floor((new Date()-propJerryLastUpdate)/60000)+'m ago' : 'tap to load'}</Text>
  </TouchableOpacity>
</View>
    </View>
   
    {/* Prop of the Day */}
    {propOfDay && (
      <View style={{backgroundColor:'rgba(255,184,0,0.08)',borderRadius:14,padding:14,marginBottom:16,borderWidth:1.5,borderColor:'rgba(255,184,0,0.4)'}}>
        <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
          <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14}}>⭐ PROP OF THE DAY</Text>
          <View style={{backgroundColor:'rgba(0,229,160,0.15)',borderRadius:8,paddingHorizontal:8,paddingVertical:3}}>
            <Text style={{color:'#00e5a0',fontSize:11,fontWeight:'800'}}>{propOfDay.ev?.toFixed(1)}% EV</Text>
          </View>
        </View>
        <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:15,marginBottom:4}}>{propOfDay.player} — {propOfDay.market} {propOfDay.side} {propOfDay.line > 0 ? '+' : ''}{propOfDay.line}</Text>
        <Text style={{color:'#7a92a8',fontSize:11,marginBottom:10}}>{propOfDay.game} • {propOfDay.book}</Text>
        <Text style={{color:'#c8d8e8',fontSize:13,lineHeight:20,fontStyle:'italic'}}>"{propOfDay.narrative?.replace(/\*\*(.*?)\*\*/g, '$1').replace(/\*(.*?)\*/g, '$1').replace(/#{1,6}\s/g, '').trim()}"</Text>
        {propOfDay.signals?.length > 0 && (
          <View style={{flexDirection:'row',gap:6,marginTop:10,flexWrap:'wrap'}}>
            {propOfDay.signals.map((sig: string, i: number) => (
              <View key={i} style={{backgroundColor:'rgba(255,184,0,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3}}>
                <Text style={{color:HRB_COLOR,fontSize:10,fontWeight:'700'}}>{sig}</Text>
              </View>
            ))}
          </View>
        )}
      </View>
    )}

    {/* Sport Selector - no NCAAB */}
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:14}}>
      <View style={{flexDirection:'row',gap:6}}>
        {['MLB','NBA','NFL','NHL','UFC'].map(s=>(
          <TouchableOpacity key={s} style={[styles.chipBtn,propJerrySport===s&&styles.chipBtnActive]} onPress={()=>{setPropJerrySport(s);fetchPropJerry(s);}}>
            <Text style={[styles.chipTxt,propJerrySport===s&&styles.chipTxtActive]}>{SPORT_EMOJI[s]} {s}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </ScrollView>

    {/* MLB: pipeline-driven props */}
    {propJerrySport === 'MLB' ? (
      pipelineMLBLoading ? (
        <View style={{alignItems:'center',paddingTop:40}}>
          <ActivityIndicator size="large" color={HRB_COLOR}/>
          <Text style={{color:'#7a92a8',marginTop:12}}>Loading pipeline matchup edges...</Text>
        </View>
      ) : pipelineMLBProps.length === 0 ? (
        <View style={{alignItems:'center',paddingTop:40}}>
          <Text style={{fontSize:32}}>🎤</Text>
          <Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>No pipeline props yet today.{'\n'}Generated after 2pm ET when lineups confirm.</Text>
        </View>
      ) : (
        <>
          <Text style={{color:'#4a6070',fontSize:11,marginBottom:12,textAlign:'center'}}>
            {pipelineMLBProps.length} matchup edges • Model-driven, no market filter
          </Text>
          {pipelineMLBProps.map((prop, i) => {
            const tierColor = prop.tier === 'PRIME' ? '#00e5a0' : prop.tier === 'STRONG' ? HRB_COLOR : '#7a92a8';
            const propLabel = prop.prop_type === 'ks_over' ? `Over ${prop.prop_line} Strikeouts` : prop.prop_type === 'hits_over' ? 'Over 0.5 Hits' : prop.prop_type;
            const signals = prop.signals || {};
            const signalEntries = Object.entries(signals);
            return (
              <View key={prop.id || i} style={[styles.card, {marginBottom:10, borderLeftWidth:3, borderLeftColor:tierColor}]}>
                <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start',marginBottom:10}}>
                  <View style={{flex:1, marginRight:12}}>
                    <Text style={{color:'#e8f0f8', fontWeight:'800', fontSize:15}}>{prop.player_name}</Text>
                    <Text style={{color:tierColor, fontWeight:'700', fontSize:13, marginTop:2}}>{propLabel}</Text>
                    <Text style={{color:'#4a6070', fontSize:11, marginTop:2}}>{prop.matchup}</Text>
                  </View>
                  <View style={{alignItems:'center'}}>
                    <View style={{width:56, height:56, borderRadius:28, borderWidth:2, borderColor:tierColor, alignItems:'center', justifyContent:'center', backgroundColor:tierColor+'15'}}>
                      <Text style={{color:tierColor, fontWeight:'900', fontSize:20}}>{prop.conviction}</Text>
                    </View>
                    <Text style={{color:tierColor, fontSize:9, fontWeight:'800', marginTop:3, letterSpacing:0.5}}>{prop.tier}</Text>
                  </View>
                </View>

                {signalEntries.length > 0 && (
                  <View style={{backgroundColor:'#0d1419', borderRadius:10, padding:10, gap:6}}>
                    <Text style={{color:'#4a6070', fontSize:10, fontWeight:'700', marginBottom:2, letterSpacing:0.5}}>MODEL SIGNALS</Text>
                    {signalEntries.slice(0, 5).map(([key, val], j) => (
                      <View key={j} style={{flexDirection:'row', alignItems:'flex-start', gap:6}}>
                        <Text style={{color:tierColor, fontSize:11, marginTop:1}}>•</Text>
                        <Text style={{color:'#c8d8e8', fontSize:12, flex:1, lineHeight:17}}>{String(val)}</Text>
                      </View>
                    ))}
                  </View>
                )}
              </View>
            );
          })}
        </>
      )
    ) : propJerryLoading?(
      <View style={{alignItems:'center',paddingTop:40}}>
        <ActivityIndicator size="large" color={HRB_COLOR}/>
        <Text style={{color:'#7a92a8',marginTop:12}}>Jerry is finding edges...</Text>
      </View>
    ):propJerryData.length===0?(
      <View style={{alignItems:'center',paddingTop:40}}>
        <Text style={{fontSize:32}}>🎤</Text>
        <Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>No props available.{'\n'}Pull to refresh or try another sport.</Text>
      </View>
    ):(
      <>
        <Text style={{color:'#4a6070',fontSize:11,marginBottom:12,textAlign:'center'}}>{propJerryData.filter(p=>p.grade==='A'||p.grade==='B').length} top props • {propJerryData.filter(p=>p.grade==='C').length} on watch list</Text>
        {propJerryData.filter(p=>p.grade==='A'||p.grade==='B').length===0&&propJerryData.filter(p=>p.grade==='C').length===0&&(
          <View style={{alignItems:'center',paddingVertical:30}}>
            <Text style={{fontSize:32}}>🎤</Text>
            <Text style={{color:'#7a92a8',fontSize:13,marginTop:8,textAlign:'center'}}>No strong edges right now.{'\n'}Check back closer to game time.</Text>
          </View>
        )}
        {propJerryData.filter(p=>p.grade==='A'||p.grade==='B').length>0&&(
          <Text style={styles.sectionLabel}>🎤 JERRY'S BEST</Text>
        )}
        {propJerryData.filter(p=>p.grade==='A'||p.grade==='B').map((prop,i)=>(
          <View key={i} style={[styles.card,{marginBottom:10,borderLeftWidth:3,borderLeftColor:prop.gradeColor}]}>
           
            {/* Header */}
            <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start',marginBottom:10}}>
              <View style={{flex:1,marginRight:12}}>
                <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:15}}>{prop.player}</Text>
                <Text style={{color:'#7a92a8',fontSize:12,marginTop:2}}>{prop.marketLabel}</Text>
                <Text style={{color:'#4a6070',fontSize:11,marginTop:2}}>{prop.gameName}</Text>
                <View style={{flexDirection:'row',gap:4,marginTop:4,flexWrap:'wrap'}}>
                  {prop.matchupConviction >= 15 ? (
                    <View style={{backgroundColor:'rgba(255,184,0,0.12)',borderRadius:6,paddingHorizontal:6,paddingVertical:2,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
                      <Text style={{color:HRB_COLOR,fontSize:9,fontWeight:'800'}}>🎯 MATCHUP PLAY</Text>
                    </View>
                  ) : prop.bestEV > 0 ? (
                    <View style={{backgroundColor:'rgba(0,229,160,0.1)',borderRadius:6,paddingHorizontal:6,paddingVertical:2,borderWidth:1,borderColor:'rgba(0,229,160,0.3)'}}>
                      <Text style={{color:'#00e5a0',fontSize:9,fontWeight:'800'}}>📊 EV EDGE</Text>
                    </View>
                  ) : null}
                  {prop.matchupSignals?.length > 0 && prop.matchupSignals.slice(0,2).map((sig: string, j: number) => (
                    <View key={j} style={{backgroundColor:'rgba(255,255,255,0.05)',borderRadius:6,paddingHorizontal:5,paddingVertical:2,borderWidth:1,borderColor:'#1f2d3d'}}>
                      <Text style={{color:'#7a92a8',fontSize:8,fontWeight:'600'}}>{sig}</Text>
                    </View>
                  ))}
                </View>
              </View>
              <View style={{alignItems:'center'}}>
                <View style={{width:52,height:52,borderRadius:26,borderWidth:2,borderColor:prop.gradeColor,alignItems:'center',justifyContent:'center',backgroundColor:prop.gradeColor+'20'}}>
                  <Text style={{color:prop.gradeColor,fontWeight:'900',fontSize:24}}>{prop.grade}</Text>
                </View>
                <Text style={{color:prop.gradeColor,fontSize:9,fontWeight:'700',marginTop:3}}>GRADE</Text>
              </View>
            </View>

            {/* Best Bet */}
            <View style={{backgroundColor:'#151c24',borderRadius:10,padding:10,marginBottom:10}}>
              <Text style={{color:'#4a6070',fontSize:10,fontWeight:'700',marginBottom:6}}>JERRY'S PICK</Text>
              <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center'}}>
                <View>
                  <Text style={{color:prop.gradeColor,fontWeight:'800',fontSize:16}}>
                    {prop.bestSide} {prop.bestLine?.line}
                  </Text>
                  <Text style={{color:'#7a92a8',fontSize:12,marginTop:2}}>{prop.bestLine?.book}</Text>
                </View>
                <View style={{alignItems:'flex-end'}}>
                  <Text style={{color:prop.bestEV>0?'#00e5a0':'#ff4d6d',fontWeight:'800',fontSize:16}}>
                    {prop.bestEV>0?'+':''}{prop.bestEV.toFixed(1)}% EV
                  </Text>
                  <Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>
                    {prop.bestLine?.odds>0?'+':''}{prop.bestLine?.odds}
                  </Text>
                </View>
              </View>
            </View>

            {/* Meta chips */}
            <View style={{flexDirection:'row',gap:6,marginBottom:10,flexWrap:'wrap'}}>
              <View style={styles.metaChip}>
                <Text style={{color:'#7a92a8',fontSize:11}}>{prop.bookCount} books</Text>
              </View>
              <View style={[styles.metaChip,{borderColor:prop.lineRange<=0.5?'#00e5a0':'#FFB800'}]}>
                <Text style={{color:prop.lineRange<=0.5?'#00e5a0':'#FFB800',fontSize:11}}>
                  {prop.lineRange===0?'Consensus':prop.lineRange.toFixed(1)+' pt range'}
                </Text>
              </View>
              <View style={styles.metaChip}>
                <Text style={{color:'#7a92a8',fontSize:11}}>
                  Over {prop.bestOver?.line} ({prop.bestOver?.odds>0?'+':''}{prop.bestOver?.odds})
                </Text>
              </View>
              <View style={styles.metaChip}>
                <Text style={{color:'#7a92a8',fontSize:11}}>
                  Under {prop.bestUnder?.line} ({prop.bestUnder?.odds>0?'+':''}{prop.bestUnder?.odds})
                </Text>
              </View>
            </View>

             {/* Jerry quote */}
            <View style={{backgroundColor:'rgba(255,184,0,0.05)',borderRadius:8,padding:10,borderLeftWidth:2,borderLeftColor:HRB_COLOR,marginBottom:10}}>
              <Text style={{color:'#7a92a8',fontSize:12,fontStyle:'italic',lineHeight:18}}>{prop.Jerry?.replace(/\*\*(.*?)\*\*/g, '$1').replace(/\*(.*?)\*/g, '$1').replace(/#{1,6}\s/g, '').trim()}</Text>
            </View>

             {/* Action buttons */}
            {prop.grade!=='D'&&(
              <View style={{flexDirection:'row',gap:8}}>
                <TouchableOpacity
                  style={{flex:1,backgroundColor:'rgba(255,184,0,0.1)',borderRadius:10,padding:10,borderWidth:1,borderColor:HRB_COLOR,flexDirection:'row',alignItems:'center',justifyContent:'center',gap:6}}
                  onPress={()=>{
                    setForm({
                      matchup: prop.gameName,
                      pick: `${prop.player} ${prop.bestSide} ${prop.bestLine?.line}`,
                      sport: propJerrySport,
                      type: 'Prop',
                      odds: String(prop.bestLine?.odds||'-110'),
                      units: '1',
                      result: 'Pending',
                      book: prop.bestLine?.book||'',
                      notes: `Prop Jerry Grade: ${prop.grade} • EV: ${prop.bestEV.toFixed(1)}%`,
                    });
                    setModalVisible(true);
                  }}
                >
                  <Text style={{fontSize:14}}>📝</Text>
                  <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:12}}>Log Pick</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={{flex:1,backgroundColor:'rgba(0,229,160,0.1)',borderRadius:10,padding:10,borderWidth:1,borderColor:'#00e5a0',flexDirection:'row',alignItems:'center',justifyContent:'center',gap:6}}
                  onPress={()=>{
                    const leg = {
                      game: prop.gameName,
                      pick: `${prop.player} ${prop.bestSide} ${prop.bestLine?.line}`,
                      odds: String(Math.abs(parseFloat(prop.bestLine?.odds||'-110'))),
                      oddsSign: parseFloat(prop.bestLine?.odds||'-110') >= 0 ? '+' : '-',
                      sport: propJerrySport,
                      type: 'Prop',
                    };
                    setParlayLegs(prev => {
                      if(prev.find(l=>l.pick===leg.pick)) return prev;
                      return [...prev, leg];
                    });
                    setActiveTab('mybets');
                  }}
                >
                  <Text style={{fontSize:14}}>🔗</Text>
                  <Text style={{color:'#00e5a0',fontWeight:'700',fontSize:12}}>Add to Parlay</Text>
                </TouchableOpacity>
              </View>
            )}
          </View>
          ))}
        {propJerryData.filter(p=>p.grade==='C').length>0&&(
          <>
            <Text style={{color:'#7a92a8',fontWeight:'800',fontSize:13,marginTop:16,marginBottom:4}}>👀 WATCH LIST</Text>
            <Text style={{color:'#4a6070',fontSize:11,marginBottom:10}}>Lower confidence — monitor only</Text>
          </>
        )}
        {propJerryData.filter(p=>p.grade==='C').map((prop,i)=>(
          <View key={i} style={[styles.card,{marginBottom:10,borderLeftWidth:3,borderLeftColor:prop.gradeColor,opacity:0.75}]}>
            <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start',marginBottom:10}}>
              <View style={{flex:1,marginRight:12}}>
                <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:15}}>{prop.player}</Text>
                <Text style={{color:'#7a92a8',fontSize:12,marginTop:2}}>{prop.marketLabel}</Text>
                <Text style={{color:'#4a6070',fontSize:11,marginTop:2}}>{prop.gameName}</Text>
              </View>
              <View style={{alignItems:'center'}}>
                <View style={{width:52,height:52,borderRadius:26,borderWidth:2,borderColor:prop.gradeColor,alignItems:'center',justifyContent:'center',backgroundColor:prop.gradeColor+'20'}}>
                  <Text style={{color:prop.gradeColor,fontWeight:'900',fontSize:24}}>{prop.grade}</Text>
                </View>
                <Text style={{color:prop.gradeColor,fontSize:9,fontWeight:'700',marginTop:3}}>GRADE</Text>
              </View>
            </View>
            <View style={{backgroundColor:'#151c24',borderRadius:10,padding:10,marginBottom:10}}>
              <Text style={{color:'#4a6070',fontSize:10,fontWeight:'700',marginBottom:6}}>JERRY'S PICK</Text>
              <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center'}}>
                <View>
                  <Text style={{color:prop.gradeColor,fontWeight:'800',fontSize:16}}>{prop.bestSide} {prop.bestLine?.line}</Text>
                  <Text style={{color:'#7a92a8',fontSize:12,marginTop:2}}>{prop.bestLine?.book}</Text>
                </View>
                <View style={{alignItems:'flex-end'}}>
                  <Text style={{color:'#00e5a0',fontWeight:'800',fontSize:16}}>{prop.bestEV>0?'+':''}{prop.bestEV.toFixed(1)}% EV</Text>
                  <Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>{prop.bestLine?.odds>0?'+':''}{prop.bestLine?.odds}</Text>
                </View>
              </View>
            </View>
            <View style={{backgroundColor:'rgba(255,184,0,0.05)',borderRadius:8,padding:10,borderLeftWidth:2,borderLeftColor:HRB_COLOR}}>
              <Text style={{color:'#7a92a8',fontSize:12,fontStyle:'italic',lineHeight:18}}>{prop.Jerry?.replace(/\*\*(.*?)\*\*/g, '$1').replace(/\*(.*?)\*/g, '$1').replace(/#{1,6}\s/g, '').trim()}</Text>
            </View>
          </View>
        ))}
      </>
    )}
  </View>
)}


            {trendsTab==='dailydegen'&&(
  <DailyDegen
    mlbGameContext={mlbGameContext}
    nbaTeamData={nbaTeamData}
    gamesData={gamesData}
    fanmatchData={fanmatchData}
    parlayLegs={parlayLegs}
    setParlayLegs={setParlayLegs}
    setActiveTab={setActiveTab}
    setMybetsTab={setMybetsTab}
    showToast={showToast}
    ANTHROPIC_API_KEY={ANTHROPIC_API_KEY}
    supabase={supabase}
    isPlayoffMode={isPlayoffMode}
    playoffSeries={playoffSeries}
  />
)}

            {trendsTab==='fades'&&(
  <FadesScanner
    gamesData={gamesData}
    mlbGameContext={mlbGameContext}
    nbaTeamData={nbaTeamData}
    nbaInjuryData={nbaInjuryData}
    gamesSport={gamesSport}
    ANTHROPIC_API_KEY={ANTHROPIC_API_KEY}
    supabase={supabase}
  />
)}

            {trendsTab==='mytrends'&&(()=>{
              if(!jerryRecord && !jerryRecordLoading) fetchJerryRecord();
              if(jerryRecordLoading) return(
                <View style={{alignItems:'center',paddingTop:60}}>
                  <ActivityIndicator size="large" color={HRB_COLOR}/>
                  <Text style={{color:'#7a92a8',marginTop:12}}>Loading Jerry's record...</Text>
                </View>
              );
              if(!jerryRecord) return(
                <View style={{alignItems:'center',paddingTop:40}}>
                  <Text style={{fontSize:32}}>📋</Text>
                  <Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>No record data yet.{'\n'}Jerry's picks will be tracked automatically.</Text>
                </View>
              );
              const p = jerryRecord.props;
              const propTotal = p.wins + p.losses;
              const propWinRate = propTotal > 0 ? ((p.wins/propTotal)*100).toFixed(0) : '—';
              return(
                <View>
                  {/* Header */}
                  <View style={{backgroundColor:'rgba(255,184,0,0.07)',borderRadius:14,padding:14,marginBottom:16,borderWidth:1,borderColor:'rgba(255,184,0,0.25)'}}>
                    <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14,marginBottom:4}}>🎤 JERRY'S TRACK RECORD</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Model performance — updated daily</Text>
                  </View>

                  {/* NRFI Model Record — always show prominently */}
                  {(()=>{
                    const nTotal = jerryRecord.nrfi.wins + jerryRecord.nrfi.losses;
                    const nPct = nTotal > 0 ? Math.round((jerryRecord.nrfi.wins/nTotal)*100) : 0;
                    if(nTotal === 0) return(
                      <View style={[styles.card,{marginBottom:16}]}>
                        <Text style={{color:'#00e5a0',fontWeight:'800',fontSize:12}}>⚾ NRFI MODEL — LEAN TIERS</Text>
                        <Text style={{color:'#7a92a8',fontSize:13,marginTop:8}}>NRFI results loading...</Text>
                      </View>
                    );
                    return(
                      <View style={[styles.hero,{marginBottom:16}]}>
                        <View>
                          <Text style={{color:'#00e5a0',fontWeight:'800',fontSize:12}}>⚾ NRFI MODEL — LEAN TIERS</Text>
                          <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:36}}>{jerryRecord.nrfi.wins}-{jerryRecord.nrfi.losses}</Text>
                          <Text style={{color:'#7a92a8',fontSize:12,marginTop:2}}>{nPct}% hit rate on mild (70-79) + prime (90-94) leans only</Text>
                        </View>
                        <View style={{alignItems:'center'}}>
                          <View style={{width:72,height:72,borderRadius:36,borderWidth:2.5,borderColor:nPct>=55?'#00e5a0':'#ff4d6d',alignItems:'center',justifyContent:'center'}}>
                            <Text style={{color:nPct>=55?'#00e5a0':'#ff4d6d',fontWeight:'800',fontSize:20}}>{nPct}%</Text>
                          </View>
                          <Text style={{color:'#4a6070',fontSize:10,marginTop:4}}>HIT RATE</Text>
                        </View>
                      </View>
                    );
                  })()}

                  {/* Prop Jerry A-Grade Record */}
                  <View style={[styles.card,{marginBottom:16}]}>
                    <Text style={{color:'#7a92a8',fontSize:11,fontWeight:'700',marginBottom:8}}>PROP JERRY A-GRADES</Text>
                    {propTotal >= 15 ? (
                      <>
                        <View style={{flexDirection:'row',justifyContent:'space-around',marginBottom:8}}>
                          <View style={{alignItems:'center'}}>
                            <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:28}}>{p.pending + propTotal}</Text>
                            <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>TRACKED</Text>
                          </View>
                          <View style={{alignItems:'center'}}>
                            <Text style={{color:'#00e5a0',fontWeight:'800',fontSize:28}}>{p.wins}</Text>
                            <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>WINS</Text>
                          </View>
                          <View style={{alignItems:'center'}}>
                            <Text style={{color:'#ff4d6d',fontWeight:'800',fontSize:28}}>{p.losses}</Text>
                            <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>LOSSES</Text>
                          </View>
                        </View>
                        <View style={{backgroundColor:'#151c24',borderRadius:10,padding:10,alignItems:'center'}}>
                          <Text style={{color:parseFloat(propWinRate)>=55?'#00e5a0':parseFloat(propWinRate)>=50?HRB_COLOR:'#ff4d6d',fontWeight:'800',fontSize:20}}>{propWinRate}% hit rate</Text>
                          <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>{propTotal} resolved • {p.pending} pending</Text>
                        </View>
                      </>
                    ) : (
                      <Text style={{color:'#7a92a8',fontSize:13,textAlign:'center',paddingHorizontal:20,lineHeight:20}}>
                        Prop results calibrating — tracking {p.pending + propTotal} props this season. Results auto-resolve daily via box score data.
                      </Text>
                    )}
                  </View>

                  {/* By Sport breakdown — only show with 25+ resolved */}
                  {propTotal >= 15 && Object.keys(p.bySport).length > 0 && (
                    <View style={[styles.card,{marginBottom:12}]}>
                      <Text style={{color:'#4a6070',fontSize:10,fontWeight:'700',letterSpacing:1,marginBottom:10}}>A-GRADE HIT RATE BY SPORT</Text>
                      {Object.entries(p.bySport).map(([sport, rec], i) => {
                        const total = rec.wins + rec.losses;
                        const pct = total > 0 ? ((rec.wins/total)*100).toFixed(0) : '—';
                        return(
                          <View key={i} style={{flexDirection:'row',alignItems:'center',justifyContent:'space-between',paddingVertical:8,borderTopWidth:i>0?1:0,borderTopColor:'#1f2d3d'}}>
                            <View style={{flexDirection:'row',alignItems:'center',gap:8}}>
                              <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13}}>{SPORT_EMOJI[sport]||'🎯'} {sport}</Text>
                              <Text style={{color:'#7a92a8',fontSize:12}}>{rec.wins}W - {rec.losses}L</Text>
                            </View>
                            <View style={{flexDirection:'row',alignItems:'center',gap:8}}>
                              <View style={{width:60,height:4,backgroundColor:'#1f2d3d',borderRadius:2,overflow:'hidden'}}>
                                <View style={{height:'100%',width:`${pct}%`,backgroundColor:parseFloat(pct)>=55?'#00e5a0':parseFloat(pct)>=50?HRB_COLOR:'#ff4d6d',borderRadius:2}}/>
                              </View>
                              <Text style={{color:parseFloat(pct)>=55?'#00e5a0':parseFloat(pct)>=50?HRB_COLOR:'#ff4d6d',fontWeight:'800',fontSize:14,width:36,textAlign:'right'}}>{pct}%</Text>
                            </View>
                          </View>
                        );
                      })}
                    </View>
                  )}

                  {/* Recent A-Grade Picks — always show */}
                  {p.recent.length > 0 && (
                    <>
                      <Text style={styles.sectionLabel}>RECENT A-GRADE PICKS</Text>
                      {p.recent.map((prop, i) => {
                        const isPending = prop.result === 'Pending';
                        const isWin = prop.result === 'Win';
                        const isLoss = prop.result === 'Loss';
                        const borderColor = isPending ? '#4a6070' : isWin ? '#00e5a0' : '#ff4d6d';
                        const statusLabel = isPending ? 'PENDING' : isWin ? 'WIN' : 'LOSS';
                        const statusColor = isPending ? '#7a92a8' : isWin ? '#00e5a0' : '#ff4d6d';
                        return(
                          <View key={i} style={[styles.betCard,{borderLeftColor:borderColor,marginBottom:8}]}>
                            <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start'}}>
                              <View style={{flex:1}}>
                                <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13}}>{prop.player}</Text>
                                <Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>{prop.market} • {prop.game}</Text>
                                <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>{prop.best_side} {prop.best_odds>0?'+':''}{prop.best_odds} @ {prop.book}</Text>
                              </View>
                              <View style={{alignItems:'center'}}>
                                <View style={{backgroundColor:borderColor+'22',borderRadius:8,paddingHorizontal:8,paddingVertical:4,borderWidth:1,borderColor}}>
                                  <Text style={{color:statusColor,fontWeight:'800',fontSize:10}}>{statusLabel}</Text>
                                </View>
                              </View>
                            </View>
                          </View>
                        );
                      })}
                    </>
                  )}

                  {/* Best Bet History */}
                  {jerryRecord.bestBets.length > 0 && (
                    <>
                      <Text style={styles.sectionLabel}>🔒 DAILY PLAY OF THE DAY HISTORY</Text>
                      {jerryRecord.bestBets.slice(0,14).map((bet, i) => {
                        const isPending = bet.result === 'Pending';
                        const isWin = bet.result === 'Win';
                        const resultColor = isPending ? '#4a6070' : isWin ? '#00e5a0' : '#ff4d6d';
                        return(
                          <View key={i} style={[styles.betCard,{borderLeftColor:resultColor,marginBottom:8}]}>
                            <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center'}}>
                              <View style={{flex:1}}>
                                <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13}}>{bet.game}</Text>
                                <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:12,marginTop:2}}>{bet.lean || 'Model Edge'}</Text>
                                <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>{SPORT_EMOJI[bet.sport]||''} {bet.sport} • {new Date(bet.bet_date + 'T12:00:00').toLocaleDateString('en-US',{month:'short',day:'numeric'})}</Text>
                              </View>
                              <View style={{alignItems:'center',gap:4}}>
                                <View style={{width:44,height:44,borderRadius:22,borderWidth:2,borderColor:HRB_COLOR,alignItems:'center',justifyContent:'center',backgroundColor:'rgba(255,184,0,0.1)'}}>
                                  <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14}}>{bet.sweat_score||'—'}</Text>
                                </View>
                                <View style={{backgroundColor:resultColor+'22',borderRadius:6,paddingHorizontal:6,paddingVertical:2,borderWidth:1,borderColor:resultColor}}>
                                  <Text style={{color:resultColor,fontWeight:'800',fontSize:9}}>{isPending?'PENDING':isWin?'WIN':'LOSS'}</Text>
                                </View>
                              </View>
                            </View>
                          </View>
                        );
                      })}
                    </>
                  )}

                  {/* Footer note */}
                  <Text style={{color:'#4a6070',fontSize:10,textAlign:'center',marginTop:12,lineHeight:14}}>Results auto-resolve daily via MLB Stats API and BDL box scores</Text>

                  <View style={{height:20}}/>
                </View>
              );
            })()}

            {trendsTab==='clv'&&(
              <View>
                <View style={{backgroundColor:'rgba(255,184,0,0.07)',borderRadius:12,padding:12,marginBottom:14,borderWidth:1,borderColor:'rgba(255,184,0,0.25)'}}>
                  <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:12,marginBottom:4}}>🎸 CLOSING LINE VALUE vs HARD ROCK</Text>
                  <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Tracks whether you got better odds than the Hard Rock closing line. Consistently beating the close is the strongest indicator of long-term edge.</Text>
                </View>
                {clvBets.length===0?(<View style={{alignItems:'center',paddingTop:40}}><Text style={{fontSize:32}}>📈</Text><Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>Log bets with odds to track your CLV!</Text></View>):(
                  <>
                    <View style={styles.statRow}>
                      <View style={[styles.statBox,{borderColor:parseFloat(avgCLV)<=0?'rgba(0,229,160,0.3)':'rgba(255,77,109,0.3)'}]}><Text style={{fontSize:18,fontWeight:'800',color:parseFloat(avgCLV)<=0?'#00e5a0':'#ff4d6d'}}>{avgCLV}%</Text><Text style={styles.statKey}>Avg CLV</Text></View>
                      <View style={[styles.statBox,{borderColor:'rgba(255,184,0,0.3)'}]}><Text style={{fontSize:18,fontWeight:'800',color:HRB_COLOR}}>{clvPositive}/{clvBets.length}</Text><Text style={styles.statKey}>Beat Close</Text></View>
                      <View style={styles.statBox}><Text style={{fontSize:18,fontWeight:'800',color:clvPositive/clvBets.length>=0.5?'#00e5a0':'#ff4d6d'}}>{clvBets.length>0?((clvPositive/clvBets.length)*100).toFixed(0):0}%</Text><Text style={styles.statKey}>CLV Rate</Text></View>
                    </View>
                    <Text style={styles.sectionLabel}>RECENT CLV</Text>
                    {clvBets.slice(0,15).map((bet,i)=>(
                      <View key={i} style={[styles.betCard,{borderLeftColor:bet.beatClosing?'#00e5a0':'#ff4d6d'}]}>
                        <View style={styles.betTop}>
                          <View style={{flex:1}}><Text style={styles.betMatchup}>{bet.matchup}</Text><Text style={styles.betPick}>{bet.pick}</Text></View>
                          <View style={{alignItems:'flex-end',gap:3}}>
                            <Text style={{color:bet.beatClosing?'#00e5a0':'#ff4d6d',fontWeight:'700',fontSize:13}}>{bet.beatClosing?'✓ Beat Close':'✗ Missed'}</Text>
                            <Text style={{color:'#7a92a8',fontSize:11}}>Got: {bet.myOdds>0?'+':''}{bet.myOdds}</Text>
                            <Text style={{color:'#7a92a8',fontSize:11}}>Close: {bet.closingOdds>0?'+':''}{bet.closingOdds}</Text>
                          </View>
                        </View>
                        <View style={styles.betMeta}>
                          <Text style={styles.metaChip}>{bet.result}</Text>
                          <Text style={styles.metaChip}>{bet.sport}</Text>
                          <View style={[styles.metaChip,{borderColor:bet.beatClosing?'#00e5a0':'#ff4d6d'}]}><Text style={{color:bet.beatClosing?'#00e5a0':'#ff4d6d',fontSize:11}}>CLV: {bet.clv.toFixed(1)}%</Text></View>
                        </View>
                      </View>
                    ))}
                  </>
                )}
              </View>
            )}
            {trendsTab==='tournament'&&(()=>{
  const tourneyBets = bets.filter(b => b.sport === 'NCAAB');
  const settled = tourneyBets.filter(b => b.result === 'Win' || b.result === 'Loss');
  const wins = settled.filter(b => b.result === 'Win').length;
  const losses = settled.filter(b => b.result === 'Loss').length;
  const pending = tourneyBets.filter(b => b.result === 'Pending').length;
  const winRate = wins+losses > 0 ? ((wins/(wins+losses))*100).toFixed(0) : '—';
  const profit = settled.reduce((sum,b) => {
    const units = parseFloat(b.units||0) || 1;
    const odds = parseInt(b.odds) || -110;
    if(b.result==='Win') return sum + (odds > 0 ? units*(odds/100) : units*(100/Math.abs(odds)));
    if(b.result==='Loss') return sum - units;
    return sum;
  }, 0);

  // Get today's top NCAAB games from gamesData
  const ncaabGames = gamesData.filter(g => g && g.away_team && g.home_team);
// Auto-load NCAAB if no games loaded yet
if(ncaabGames.length === 0 && modelEdgeSport === 'NCAAB' && gamesSport !== 'NCAAB') {
  setGamesSport('NCAAB');
  fetchGames('NCAAB', 'today');
}
  const topGames = ncaabGames
    .map(game => {
      try {
        const score = calcGameSweatScore(game, 'NCAAB', fanmatchData);
        return score ? {game, score} : null;
      } catch(e) { return null; }
    })
    .filter(Boolean)
    .sort((a,b) => b.score.total - a.score.total)
    .slice(0, 5);

  return(
    <View>
      {/* Header */}
      <View style={{backgroundColor:'rgba(255,184,0,0.07)',borderRadius:14,padding:14,borderWidth:1,borderColor:'rgba(255,184,0,0.2)',marginBottom:16}}>
        <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14,marginBottom:4}}>🏀 2026 MARCH MADNESS TRACKER</Text>
        <Text style={{color:'#7a92a8',fontSize:12}}>Your tournament record + today's top model plays</Text>
      </View>

      {/* Record Hero */}
      <View style={[styles.hero,{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:16}]}>
        <View>
          <Text style={{color:'#7a92a8',fontSize:11,fontWeight:'700'}}>TOURNAMENT RECORD</Text>
          <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:36}}>{wins}-{losses}</Text>
          <Text style={{color:'#7a92a8',fontSize:12,marginTop:2}}>{pending} pending • {winRate}% hit rate</Text>
        </View>
        <View style={{alignItems:'center'}}>
          <Text style={{color:profit>=0?'#00e5a0':'#ff4d6d',fontWeight:'800',fontSize:28}}>
            {profit>=0?'+':''}{profit.toFixed(1)}u
          </Text>
          <Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>PROFIT</Text>
        </View>
      </View>

      {/* Today's Top Model Plays */}
      <Text style={styles.sectionLabel}>🔒 TODAY'S TOP MODEL PLAYS</Text>
      {topGames.length === 0 ? (
        <View style={{alignItems:'center',paddingVertical:30}}>
          <Text style={{fontSize:32}}>🏀</Text>
          <Text style={{color:'#7a92a8',fontSize:13,marginTop:8,textAlign:'center'}}>Switch Games tab to NCAAB to load tournament games.</Text>
        </View>
      ) : (
        topGames.map((item, i) => {
          const ss = item.score;
          const tier = ss.total >= 68 ? {label:'🔒 PRIME', color:'#FFB800'} :
                       ss.total >= 62 ? {label:'✅ LEAN', color:'#00e5a0'} :
                       {label:'👀 WATCH', color:'#0099ff'};
          const gameTime = new Date(item.game.commence_time).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true});
          return(
            <TouchableOpacity key={i} onPress={()=>{setActiveTab('games');setGamesSport('NCAAB');openGameDetail(item.game);}}
              style={{backgroundColor:'#0e1318',borderRadius:14,padding:14,marginBottom:8,borderWidth:1,borderLeftWidth:3,borderColor:'#1f2d3d',borderLeftColor:tier.color}}>
              <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:6}}>
                <View style={{flex:1}}>
                  <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14}}>{item.game.away_team} vs {item.game.home_team}</Text>
                  <Text style={{color:'#4a6070',fontSize:11,marginTop:2}}>{gameTime}</Text>
                </View>
                <View style={{alignItems:'center',marginLeft:12}}>
                  <View style={{width:44,height:44,borderRadius:22,borderWidth:2,borderColor:tier.color,alignItems:'center',justifyContent:'center',backgroundColor:tier.color+'15'}}>
                    <Text style={{color:tier.color,fontWeight:'800',fontSize:16}}>{ss.total}</Text>
                  </View>
                  <Text style={{color:tier.color,fontSize:9,fontWeight:'700',marginTop:2}}>{tier.label}</Text>
                </View>
              </View>
              {ss.leanSide&&(
                <View style={{flexDirection:'row',alignItems:'center',gap:6}}>
                  <View style={{backgroundColor:'rgba(255,184,0,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
                    <Text style={{color:HRB_COLOR,fontSize:11,fontWeight:'700'}}>📊 {ss.leanSide}</Text>
                  </View>
                  {ss.hasFanmatch&&(
                    <View style={{backgroundColor:'rgba(0,229,160,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,borderWidth:1,borderColor:'rgba(0,229,160,0.3)'}}>
                      <Text style={{color:'#00e5a0',fontSize:11,fontWeight:'700'}}>📡 KenPom Model</Text>
                    </View>
                  )}
                </View>
              )}
            </TouchableOpacity>
          );
        })
      )}

      {/* Tournament Bets Log */}
      {tourneyBets.length > 0 && (
        <>
          <Text style={styles.sectionLabel}>📋 TOURNAMENT BETS</Text>
          {tourneyBets.slice(0,20).map((bet,i) => (
            <View key={i} style={[styles.betCard,{borderLeftColor:bet.result==='Win'?'#00e5a0':bet.result==='Loss'?'#ff4d6d':'#4a6070'}]}>
              <View style={styles.betTop}>
                <View style={{flex:1}}>
                  <Text style={styles.betMatchup}>{bet.matchup}</Text>
                  <Text style={styles.betPick}>{bet.pick}</Text>
                </View>
                <View style={{alignItems:'flex-end',gap:3}}>
                  <Text style={{color:resultColor(bet.result),fontWeight:'700',fontSize:13}}>{bet.result}</Text>
                  <Text style={{color:'#7a92a8',fontSize:11}}>{bet.units}u • {bet.odds>0?'+':''}{bet.odds}</Text>
                </View>
              </View>
            </View>
          ))}
        </>
      )}
      <View style={{height:20}}/>
    </View>
  );
})()}
            <View style={{height:20}}/>
          </View>
        )}

        {activeTab==='odds'&&(
          <View>
            <Text style={styles.pageTitle}>Live Odds</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:14}}>
              <View style={{flexDirection:'row',gap:6}}>{SPORTS.map(s=>(<TouchableOpacity key={s} style={[styles.chipBtn,oddsSport===s&&styles.chipBtnActive]} onPress={()=>setOddsSport(s)}><Text style={[styles.chipTxt,oddsSport===s&&styles.chipTxtActive]}>{s}</Text></TouchableOpacity>))}</View>
            </ScrollView>
            {oddsLoading?(<View style={{alignItems:'center',paddingTop:60}}><ActivityIndicator size="large" color={HRB_COLOR}/></View>):
            oddsData.length===0?(<View style={{alignItems:'center',paddingTop:60}}><Text style={{fontSize:32}}>🏆</Text><Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>No games for {oddsSport} right now.</Text></View>):(
              oddsData.map((game,i)=>{
                const bestBook=getBestSpread(game);
                const sortedBooks=game.bookmakers?[...game.bookmakers].sort((a,b)=>{
                  const aIsHRB=(BOOKMAKER_MAP[a.key]||a.key)===HRB;
                  const bIsHRB=(BOOKMAKER_MAP[b.key]||b.key)===HRB;
                  if(aIsHRB)return -1; if(bIsHRB)return 1; return 0;
                }):[];
                return(
                  <View key={i} style={styles.card}>
                    <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
                      <View style={{flex:1}}><Text style={styles.cardTitle}>{game.away_team} @ {game.home_team}</Text><Text style={styles.cardSub}>{new Date(game.commence_time).toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'})} • {new Date(game.commence_time).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'})}</Text></View>
                      <View style={[styles.pill,{backgroundColor:'rgba(0,153,255,0.15)'}]}><Text style={{color:'#0099ff',fontSize:11,fontWeight:'700'}}>{oddsSport}</Text></View>
                    </View>
                    <View style={{flexDirection:'row',justifyContent:'space-between',marginBottom:6,paddingHorizontal:4}}>
                      <Text style={styles.oddsHeader}>BOOK</Text><Text style={styles.oddsHeader}>SPREAD</Text><Text style={styles.oddsHeader}>TOTAL</Text>
                    </View>
                    {sortedBooks.slice(0,7).map((bm,j)=>{
                      const spreadMkt=bm.markets&&bm.markets.find(m=>m.key==='spreads');
                      const totalMkt=bm.markets&&bm.markets.find(m=>m.key==='totals');
                      const bookName=BOOKMAKER_MAP[bm.key]||bm.key;
                      const isBest=bookName===bestBook;
                      const isHRB=bookName===HRB;
                      return(
                        <View key={j} style={[styles.oddsRow,isHRB&&{backgroundColor:'rgba(255,184,0,0.06)',borderRadius:8,paddingHorizontal:4},isBest&&!isHRB&&styles.oddsRowBest]}>
                          <Text style={[styles.bookNameInline,isHRB&&{color:HRB_COLOR,fontWeight:'800'},isBest&&!isHRB&&{color:'#00e5a0'}]} numberOfLines={1}>{isHRB?'🎸 HRB':bookName}{isBest&&!isHRB?' ★':''}</Text>
                          <View style={{flex:1,alignItems:'center'}}><Text style={[styles.oddsVal,isHRB&&{color:HRB_COLOR},isBest&&!isHRB&&{color:'#00e5a0'}]}>{spreadMkt?formatSpread(spreadMkt.outcomes):'N/A'}</Text><Text style={styles.oddsSmall}>{spreadMkt?formatOdds(spreadMkt.outcomes):''}</Text></View>
                          <View style={{alignItems:'flex-end'}}><Text style={[styles.oddsVal,isHRB&&{color:HRB_COLOR}]}>{totalMkt&&totalMkt.outcomes?'O/U '+totalMkt.outcomes[0].point:'N/A'}</Text><Text style={styles.oddsSmall}>{totalMkt?formatOdds(totalMkt.outcomes):''}</Text></View>
                        </View>
                      );
                    })}
                    {bestBook&&<View style={{marginTop:8,padding:8,backgroundColor:bestBook===HRB?'rgba(255,184,0,0.1)':'rgba(0,229,160,0.07)',borderRadius:8,borderWidth:1,borderColor:bestBook===HRB?'rgba(255,184,0,0.3)':'transparent'}}><Text style={{fontSize:11,color:bestBook===HRB?HRB_COLOR:'#00e5a0'}}>{bestBook===HRB?'🎸 Hard Rock has the best spread!':'💡 Best spread: '+bestBook}</Text></View>}
                  </View>
                );
              })
            )}
          </View>
        )}

        {activeTab==='stats'&&(
          <View>
            <Text style={styles.pageTitle}>Stats & Props</Text>
            <View style={{flexDirection:'row',gap:8,marginBottom:14}}>
              {[{id:'props',label:'🎯 Props'},{id:'players',label:'📊 Players'},{id:'teams',label:'🏆 Board'}].map(t=>(
                <TouchableOpacity key={t.id} style={[styles.chipBtn,statsTab===t.id&&styles.chipBtnActive,{flex:1,alignItems:'center'}]} onPress={()=>setStatsTab(t.id)}>
                  <Text style={[styles.chipTxt,statsTab===t.id&&styles.chipTxtActive]}>{t.label}</Text>
                </TouchableOpacity>
              ))}
            </View>
            {statsTab==='props'&&(
              <>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:14}}>
                  <View style={{flexDirection:'row',gap:6}}>{['NBA','NFL','NHL','MLB'].map(s=>(<TouchableOpacity key={s} style={[styles.chipBtn,propsSport===s&&styles.chipBtnActive]} onPress={()=>setPropsSport(s)}><Text style={[styles.chipTxt,propsSport===s&&styles.chipTxtActive]}>{SPORT_EMOJI[s]} {s}</Text></TouchableOpacity>))}</View>
                </ScrollView>
                <View style={{flexDirection:'row',alignItems:'center',backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',borderRadius:12,paddingHorizontal:12,marginBottom:14}}>
  <Text style={{fontSize:16,marginRight:8}}>🔍</Text>
  <TextInput
    style={{flex:1,color:'#e8f0f8',fontSize:14,paddingVertical:10}}
    placeholder="Search players..."
    placeholderTextColor="#4a6070"
    value={propsSearch}
    onChangeText={setPropsSearch}
    returnKeyType="search"
  />
  {propsSearch.length>0&&(
    <TouchableOpacity onPress={()=>setPropsSearch('')}>
      <Text style={{color:'#4a6070',fontSize:16}}>✕</Text>
    </TouchableOpacity>
  )}
</View>
                {propsLoading?<View style={{alignItems:'center',paddingTop:60}}><ActivityIndicator size="large" color={HRB_COLOR}/></View>:
                propsData.length===0?<View style={{alignItems:'center',paddingTop:60}}><Text style={{fontSize:32}}>🎯</Text><Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>No props available right now.</Text></View>:
                propsData.filter(prop=>
  propsSearch===''||
  prop.player.toLowerCase().includes(propsSearch.toLowerCase())
).map((prop,i)=>{
                  const bestLine=getBestPropLine(prop.lines);
                  const hrbPropLine=prop.lines.find(l=>l.book===HRB);
                  return(
                     <TouchableOpacity key={i} onPress={()=>{setSelectedPropPlayer({name:prop.player,team:'',position:'',line:prop.line||prop.overUnder||0});setPropHistoryStat('pts');fetchPropHistory({name:prop.player});setPropHistoryModal(true);}}>
                      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start',marginBottom:6}}>
                        <View style={{flex:1}}><Text style={styles.betMatchup}>{prop.player}</Text><Text style={styles.betPick}>{prop.market} • {prop.gameName}</Text></View>
                        <View style={{alignItems:'flex-end'}}>
                          {hrbPropLine?<Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:16}}>🎸 {hrbPropLine.line}</Text>:bestLine?<Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:16}}>O/U {bestLine.line}</Text>:null}
                          {hrbPropLine?<Text style={{color:HRB_COLOR,fontSize:11}}>Hard Rock</Text>:bestLine?<Text style={{color:'#7a92a8',fontSize:11}}>{bestLine.book}</Text>:null}
                        </View>
                      </View>
                      <View style={{flexDirection:'row',gap:6,flexWrap:'wrap',marginTop:4}}>
                        {prop.lines.map((l,j)=>(
                          <View key={j} style={[styles.metaChip,l.book===HRB&&{borderColor:HRB_COLOR}]}>
                            <Text style={[{fontSize:11,color:'#7a92a8'},l.book===HRB&&{color:HRB_COLOR}]}>{l.book===HRB?'🎸 ':''}{l.book}: {l.line} ({l.odds>0?'+':''}{l.odds})</Text>
                          </View>
                        ))}
                      </View>
                    </TouchableOpacity>
                  );
                })}
              </>
            )}
            {statsTab==='players'&&(
  <View style={{alignItems:'center',paddingTop:60,paddingHorizontal:24}}>
    <Text style={{fontSize:40}}>📊</Text>
    <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:18,marginTop:16,textAlign:'center'}}>Player Profiles</Text>
    <Text style={{color:'#7a92a8',fontSize:13,marginTop:8,textAlign:'center',lineHeight:20}}>Deep player stats, game logs, and trend analysis coming soon.{'\n\n'}Upgrade to Pro to get early access when it launches.</Text>
    <View style={{marginTop:20,backgroundColor:'rgba(255,184,0,0.1)',borderRadius:12,padding:14,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
      <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:12,textAlign:'center'}}>🔜 COMING IN V1.1</Text>
    </View>
  </View>
)}
{statsTab==='teams'&&(()=>{
  const ncaabGames = gamesData.filter(g => g && g.away_team && g.home_team);
  const scored = ncaabGames
    .map(game => {
      try {
        const score = calcGameSweatScore(game, gamesSport, fanmatchData, mlbGameContext, nbaTeamData);
        return score ? {game, score} : null;
      } catch(e) { return null; }
    })
    .filter(Boolean)
    .sort((a,b) => b.score.total - a.score.total)
    .slice(0, 10);

  return(
    <View>
      <View style={{backgroundColor:'rgba(255,184,0,0.06)',borderRadius:14,padding:14,borderWidth:1,borderColor:'rgba(255,184,0,0.2)',marginBottom:12}}>
  <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:12,marginBottom:4}}>🏆 MODEL LEADERBOARD</Text>
  <Text style={{color:'#7a92a8',fontSize:12}}>Today's top plays ranked by Sweat Score</Text>
</View>
<ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:12}}>
  <View style={{flexDirection:'row',gap:6}}>
    {SPORTS.map(s=>(
      <TouchableOpacity key={s} 
        style={[styles.chipBtn, gamesSport===s&&styles.chipBtnActive]} 
        onPress={()=>{setGamesSport(s); fetchGames(s, 'today');}}>
        <Text style={[styles.chipTxt, gamesSport===s&&styles.chipTxtActive]}>{SPORT_EMOJI[s]} {s}</Text>
      </TouchableOpacity>
    ))}
  </View>
</ScrollView>
      {scored.length === 0 ? (
        <View style={{alignItems:'center',paddingTop:40}}>
          <Text style={{fontSize:32}}>🏆</Text>
          <Text style={{color:'#7a92a8',fontSize:13,marginTop:8,textAlign:'center'}}>Switch to a sport tab with games to see the leaderboard.</Text>
        </View>
      ) : (
        scored.map((item, i) => {
          const ss = item.score;
          const tier = ss.total >= 68 ? {label:'🔒 PRIME', color:'#FFB800'} :
                       ss.total >= 55 ? {label:'✅ LEAN', color:'#00e5a0'} :
                       {label:'👀 WATCH', color:'#0099ff'};
          const gameTime = new Date(item.game.commence_time).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true});
          return(
            <TouchableOpacity key={i} onPress={()=>openGameDetail(item.game)}
              style={{backgroundColor:'#0e1318',borderRadius:14,padding:14,marginBottom:8,borderWidth:1,borderLeftWidth:3,borderColor:'#1f2d3d',borderLeftColor:tier.color}}>
              <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:6}}>
                <View style={{flex:1}}>
                  <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14}}>{item.game.away_team} vs {item.game.home_team}</Text>
                  <Text style={{color:'#4a6070',fontSize:11,marginTop:2}}>{gamesSport} • {gameTime}</Text>
                </View>
                <View style={{alignItems:'center',marginLeft:12}}>
                  <View style={{width:44,height:44,borderRadius:22,borderWidth:2,borderColor:tier.color,alignItems:'center',justifyContent:'center',backgroundColor:tier.color+'15'}}>
                    <Text style={{color:tier.color,fontWeight:'800',fontSize:16}}>{ss.total}</Text>
                  </View>
                  <Text style={{color:tier.color,fontSize:9,fontWeight:'700',marginTop:2}}>{tier.label}</Text>
                </View>
              </View>
              {ss.leanSide && (
                <View style={{flexDirection:'row',alignItems:'center',gap:8}}>
                  <View style={{backgroundColor:'rgba(255,184,0,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
                    <Text style={{color:HRB_COLOR,fontSize:11,fontWeight:'700'}}>📊 {ss.leanSide}</Text>
                  </View>
                  {ss.totalIsPrimary && (
                    <View style={{backgroundColor:'rgba(0,153,255,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,borderWidth:1,borderColor:'rgba(0,153,255,0.3)'}}>
                      <Text style={{color:'#0099ff',fontSize:11,fontWeight:'700'}}>📈 Total Signal</Text>
                    </View>
                  )}
                </View>
              )}
            </TouchableOpacity>
          );
        })
      )}
      <View style={{height:20}}/>
    </View>
  );
})()}
      </View>
        )}
        {(activeTab==='parlay'||(activeTab==='mybets'&&mybetsTab==='parlay_sub'))&&(
          <View>
            {activeTab==='mybets'&&(
              <View style={{flexDirection:'row',marginBottom:14,gap:0,backgroundColor:'#151c24',borderRadius:12,overflow:'hidden'}}>
                <TouchableOpacity style={{flex:1,paddingVertical:10,alignItems:'center',backgroundColor:mybetsTab==='picks'?'#1a2a3a':'transparent'}} onPress={()=>setMybetsTab('picks')}>
                  <Text style={{color:mybetsTab==='picks'?'#00e5a0':'#7a92a8',fontWeight:'700',fontSize:13}}>My Picks</Text>
                </TouchableOpacity>
                <TouchableOpacity style={{flex:1,paddingVertical:10,alignItems:'center',backgroundColor:mybetsTab==='parlay_sub'?'#1a2a3a':'transparent'}} onPress={()=>setMybetsTab('parlay_sub')}>
                  <Text style={{color:mybetsTab==='parlay_sub'?'#00e5a0':'#7a92a8',fontWeight:'700',fontSize:13}}>Parlay Builder</Text>
                </TouchableOpacity>
              </View>
            )}
            <Text style={styles.pageTitle}>Parlay Builder</Text>
            <View style={[styles.hero,{flexDirection:'column',alignItems:'stretch'}]}>
              <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
                <View>
                  <Text style={styles.heroSub}>{parlayLegs.length}-Leg Parlay</Text>
                  <Text style={{fontSize:36,fontWeight:'800',color:parlayLegs.length>0?'#00e5a0':'#4a6070'}}>{parlayAmerican}</Text>
                  <Text style={styles.heroMeta}>Implied prob: {parlayProb}%</Text>
                </View>
                <View style={{alignItems:'flex-end'}}>
                  <Text style={{fontSize:12,color:'#7a92a8',fontWeight:'600'}}>PAYOUT</Text>
                  <Text style={{fontSize:28,fontWeight:'800',color:'#e8f0f8'}}>${parlayPayout}</Text>
                  <Text style={{fontSize:12,color:'#00e5a0',fontWeight:'600'}}>+${parlayProfit} profit</Text>
                </View>
              </View>
              <View style={{flexDirection:'row',alignItems:'center',backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',borderRadius:12,paddingHorizontal:14,paddingVertical:10}}>
                <Text style={{color:'#00e5a0',fontWeight:'700',fontSize:16,marginRight:8}}>$</Text>
                <TextInput style={{flex:1,color:'#e8f0f8',fontSize:16,fontWeight:'600'}} placeholder="Enter wager" placeholderTextColor="#4a6070" value={parlayWager} onChangeText={setParlayWager} keyboardType="numeric" returnKeyType="done"/>
                <Text style={{color:'#7a92a8',fontSize:12}}>WAGER</Text>
              </View>
            </View>
            {parlayLegs.length===0?(<View style={{alignItems:'center',paddingTop:20,paddingBottom:20}}><Text style={{fontSize:32}}>🎰</Text><Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>No legs added yet.{'\n'}Browse games or add manually!</Text></View>):(
              <>{<Text style={styles.sectionLabel}>PARLAY LEGS</Text>}
              {parlayLegs.map((leg)=>{
                const legDecimal=americanToDecimal(leg.oddsSign+leg.odds);
                const legProb=((1/legDecimal)*100).toFixed(0);
                return(<View key={leg.id} style={[styles.betCard,{borderLeftColor:'#00e5a0'}]}><View style={styles.betTop}><View style={{flex:1}}><Text style={styles.betMatchup}>{leg.matchup}</Text><Text style={styles.betPick}>{leg.pick}</Text></View><View style={{alignItems:'flex-end',gap:4}}><Text style={{color:'#00e5a0',fontWeight:'700',fontSize:16}}>{leg.oddsSign}{leg.odds}</Text><Text style={{color:'#7a92a8',fontSize:11}}>{legProb}% prob</Text><TouchableOpacity onPress={()=>removeLeg(leg.id)}><Text style={{color:'#ff4d6d',fontSize:11}}>✕ Remove</Text></TouchableOpacity></View></View></View>);
              })}</>
            )}
            <TouchableOpacity style={[styles.btnPrimary,{backgroundColor:'rgba(0,153,255,0.15)',borderWidth:1,borderColor:'#0099ff'}]} onPress={()=>setActiveTab('games')}><Text style={[styles.btnPrimaryText,{color:'#0099ff'}]}>🏟 Browse Games to Add Legs</Text></TouchableOpacity>
            <TouchableOpacity style={[styles.btnPrimary,{backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d'}]} onPress={()=>setAddLegModal(true)}><Text style={[styles.btnPrimaryText,{color:'#7a92a8'}]}>+ Add Leg Manually</Text></TouchableOpacity>
            {parlayLegs.length>=2&&(
                <TouchableOpacity
                  style={[styles.btnPrimary,{backgroundColor:'rgba(255,184,0,0.1)',borderWidth:1,borderColor:HRB_COLOR}]}
                  onPress={fetchParlayAnalysis}
                >
                  <Text style={[styles.btnPrimaryText,{color:HRB_COLOR}]}>🎤 Get Jerry's Analysis</Text>
                </TouchableOpacity>
              )}
            {parlayLegs.length>0&&(
              <>
                <TouchableOpacity style={[styles.btnPrimary,{backgroundColor:'#151c24',borderWidth:1,borderColor:'#00e5a0'}]} onPress={()=>{
                  setBets(prev=>[{id:Date.now(),matchup:parlayLegs.length+'-Leg Parlay',pick:parlayLegs.map(l=>l.pick).join(' + '),sport:'Parlay',type:'Parlay',odds:parlayAmerican,units:(parseFloat(parlayWager)/10).toFixed(1),book:'Hard Rock',result:'Pending',date:new Date().toLocaleDateString('en-US',{month:'short',day:'numeric'})},...prev]);
                  setParlayLegs([]);Alert.alert('✅ Saved!','Parlay added to My Picks.');
                }}><Text style={[styles.btnPrimaryText,{color:'#00e5a0'}]}>📋 Save to My Picks</Text></TouchableOpacity>
                <TouchableOpacity style={[styles.btnPrimary,{backgroundColor:'transparent',borderWidth:1,borderColor:'#ff4d6d'}]} onPress={()=>setParlayLegs([])}><Text style={[styles.btnPrimaryText,{color:'#ff4d6d'}]}>🗑 Clear Parlay</Text></TouchableOpacity>
              </>
            )}
            <View style={{height:20}}/>
          </View>
        )}
        <View style={{height:20}}/>
      </ScrollView>

      <View style={{backgroundColor:'#0a1018',paddingHorizontal:16,paddingVertical:8,borderTopWidth:1,borderTopColor:'#1f2d3d'}}>
              <Text style={{color:'#4a6070',fontSize:9,textAlign:'center',lineHeight:14}}>⚠️ For entertainment only. The Sweat Locker provides data analysis and does not faciliate wagering of any kind. Past performance is not indicative of future results.{'  '}

                {'  •  '}
                <Text style={{color:'#FFB800'}} onPress={()=>Linking.openURL('tel:18882364848')}></Text>
              </Text>
            </View>
            <View style={styles.bottomNavContainer}></View>
      <View style={styles.bottomNavContainer}>
        <View style={{flexDirection:'row',justifyContent:'space-evenly'}}>
          {[{id:'home',icon:'📊',label:'Home'},{id:'games',icon:'🏟',label:'Games'},{id:'jerry',icon:'🧠',label:'Jerry'},{id:'mybets',icon:'🎯',label:'My Bets'},{id:'odds',icon:'💰',label:'Odds'}].map(tab=>(
            <TouchableOpacity key={tab.id} style={styles.tabItem} onPress={()=>setActiveTab(tab.id)}>
              <Text style={styles.tabIcon}>{tab.icon}</Text>
              {tab.id==='parlay'&&parlayLegs.length>0?(
                <View style={{position:'relative'}}><Text style={[styles.tabLabel,activeTab===tab.id&&{color:HRB_COLOR}]}>{tab.label}</Text><View style={styles.tabBadge}><Text style={styles.tabBadgeText}>{parlayLegs.length}</Text></View></View>
              ):(<Text style={[styles.tabLabel,activeTab===tab.id&&{color:HRB_COLOR}]}>{tab.label}</Text>)}
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {selectedGame&&(
        <Modal visible={gameDetailModal} animationType="slide" transparent>
          <View style={styles.modalOverlay}>
            <View style={[styles.modalSheet,{maxHeight:'92%'}]}>
              <View style={styles.modalHandle}/>
              <Text style={[styles.modalTitle, {fontSize:18}]} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.6}>{stripMascot(selectedGame.away_team)} @ {stripMascot(selectedGame.home_team)}</Text>
              <Text style={{color:'#7a92a8',fontSize:12,marginTop:-10,marginBottom:16}}>{new Date(selectedGame.commence_time).toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'})} • {new Date(selectedGame.commence_time).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',timeZone:'America/New_York'})} ET</Text>
              <ScrollView showsVerticalScrollIndicator={false}>
                
                {(()=>{
                  const isLive = new Date(selectedGame.commence_time) <= new Date();
                  const ss = getSweatScoreForGame(selectedGame, gamesSport);
                  if(!ss) return null;
                  const tier = getSweatTier(ss.total);
                  const isExpanded = expandedSweatScore==='main';
                  return(
                    <View style={{backgroundColor:'#0a1018',borderRadius:16,padding:16,marginBottom:16,borderWidth:1.5,borderColor:tier.color+'66'}}>
                      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
                        <View>
                          <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:16}}>🧠 SWEAT SCORE</Text>
                          <Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>Algorithmic confidence rating</Text>
                        </View>
                        <View style={{alignItems:'center',gap:2}}>
                          <View style={{width:64,height:64,borderRadius:32,borderWidth:2.5,borderColor:tier.color,alignItems:'center',justifyContent:'center',backgroundColor:tier.color+'15'}}>
                            <Text style={{color:tier.color,fontWeight:'800',fontSize:24}}>{ss.total}</Text>
                          </View>
                          <Text style={{color:tier.color,fontSize:10,fontWeight:'800'}}>{tier.label}</Text>
                        </View>
                      </View>
                       {ss.leanSide&&(
                        <View style={{flexDirection:'row',alignItems:'center',gap:8,backgroundColor:'rgba(255,184,0,0.1)',borderRadius:10,padding:10,marginBottom:8,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
  <Text style={{color:'#4a6070',fontSize:11,fontWeight:'700'}}>MODEL LEAN</Text>
  <Text adjustsFontSizeToFit numberOfLines={1} minimumFontScale={0.6} style={{color:HRB_COLOR,fontWeight:'800',fontSize:15,flex:1}}>{ss.leanSide}</Text>
</View>
                      )}
                      {(()=>{
                         if(gamesSport!=='NCAAB') return null;
                        if(!Object.keys(fanmatchData).length) return null;
                        const awayNorm = normalizeTeamName(selectedGame?.away_team||'');
                        const homeNorm = normalizeTeamName(selectedGame?.home_team||'');
                        //console.log('Fanmatch keys:', Object.keys(fanmatchData).slice(0,3));
                        //console.log('Looking for:', selectedGame?.away_team, selectedGame?.home_team);
                       const awayStripped = stripMascot(selectedGame?.away_team||'');
                        const homeStripped = stripMascot(selectedGame?.home_team||'');
                        //console.log('Stripped:', awayStripped, homeStripped);
                        const fmKey = Object.keys(fanmatchData).find(k => {
                          const [v,h] = k.split('_');
                           return (fuzzyMatch(v, awayStripped)>0.7 && fuzzyMatch(h, homeStripped)>0.7)
                                
                        });
                        //console.log('fmKey found:', fmKey);
                        const fm = fmKey ? fanmatchData[fmKey] : null;
                        if(!fm) return null;
                        return(
                          <View style={{backgroundColor:'rgba(0,229,160,0.07)',borderRadius:10,padding:10,marginBottom:12,borderWidth:1,borderColor:'rgba(0,229,160,0.2)'}}>
                            <Text style={{color:'#4a6070',fontSize:10,fontWeight:'700',marginBottom:6}}>📡 MODEL PREDICTION</Text>
                            <View style={{flexDirection:'row',justifyContent:'space-around',marginBottom:8}}>
                              <View style={{alignItems:'center'}}>
                                <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:22}}>{Math.round(fm.visitorPred)}</Text>
                                <Text style={{color:'#7a92a8',fontSize:10,marginTop:2}}>{stripMascot(selectedGame.away_team)}</Text>
                              </View>
                              <View style={{alignItems:'center',justifyContent:'center'}}>
                                <Text style={{color:'#4a6070',fontSize:16,fontWeight:'700'}}>@</Text>
                              </View>
                              <View style={{alignItems:'center'}}>
                                <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:22}}>{Math.round(fm.homePred)}</Text>
                                <Text style={{color:'#7a92a8',fontSize:10,marginTop:2}}>{stripMascot(selectedGame.home_team)}</Text>
                              </View>
                            </View>
                            <View style={{flexDirection:'row',justifyContent:'space-around',borderTopWidth:1,borderTopColor:'#1f2d3d',paddingTop:8}}>
                              <View style={{alignItems:'center'}}>
                                <Text style={{color:'#00e5a0',fontWeight:'700',fontSize:13}}>{Math.round(fm.homeWP)}%</Text>
                                <Text style={{color:'#4a6070',fontSize:9}}>HOME WIN PROB</Text>
                              </View>
                              <View style={{alignItems:'center'}}>
                                <Text style={{color:'#00e5a0',fontWeight:'700',fontSize:13}}>{Math.round(fm.predTempo)}</Text>
                                <Text style={{color:'#4a6070',fontSize:9}}>PRED TEMPO</Text>
                              </View>
                              <View style={{alignItems:'center'}}>
                                <Text style={{color:'#00e5a0',fontWeight:'700',fontSize:13}}>{fm.thrillScore?.toFixed(1)}</Text>
                                <Text style={{color:'#4a6070',fontSize:9}}>THRILL SCORE</Text>
                              </View>
                            </View>
                          </View>
                        );
                      })()}
                      <Text style={{color:'#b0c4d8',fontSize:13,lineHeight:20,marginBottom:12}}>{ss.narrative}</Text>
                      {/* Best Bets */}
                      <View style={{backgroundColor:'#151c24',borderRadius:12,padding:12,marginBottom:12}}>
                        <Text style={{color:'#4a6070',fontSize:10,fontWeight:'700',marginBottom:8}}>
                          {ss.spreadBet?.book===HRB && ss.totalBet?.book===HRB && ss.mlBet?.book===HRB
                            ? '🎸 BEST BETS ON HARD ROCK'
                            : '🎯 BEST AVAILABLE LINES'}
                        </Text>
                        <View style={{flexDirection:'row',gap:6}}>
                          {ss.spreadBet&&<TouchableOpacity style={{flex:1,backgroundColor:'rgba(255,184,0,0.1)',borderRadius:10,padding:8,alignItems:'center',borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}} onPress={()=>{setForm({matchup:selectedGame.away_team+' vs '+selectedGame.home_team,pick:ss.spreadBet.pick,sport:gamesSport,type:'Spread',odds:String(ss.spreadBet.odds),units:'',book:HRB,result:'Pending'});setGameDetailModal(false);setModalVisible(true);}}>
                            <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>SPREAD</Text>
                            <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:13,marginTop:3}}>{ss.spreadBet.pick}</Text>
                            <Text style={{color:'#7a92a8',fontSize:11}}>{ss.spreadBet.odds>0?'+':''}{ss.spreadBet.odds}</Text>
                           <Text style={{color:ss.spreadBet.book===HRB?HRB_COLOR:'#4a6070',fontSize:9,fontWeight:'700',marginTop:2}}>{ss.spreadBet.book===HRB?'🎸 HRB':ss.spreadBet.book}</Text>
                          </TouchableOpacity>}
                          {ss.totalBet&&<TouchableOpacity style={{flex:1,backgroundColor:'rgba(255,184,0,0.1)',borderRadius:10,padding:8,alignItems:'center',borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}} onPress={()=>{setForm({matchup:selectedGame.away_team+' vs '+selectedGame.home_team,pick:ss.totalBet.pick,sport:gamesSport,type:'Total (O/U)',odds:String(ss.totalBet.odds),units:'',book:HRB,result:'Pending'});setGameDetailModal(false);setModalVisible(true);}}>
                            <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>TOTAL</Text>
                            <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:13,marginTop:3}}>{ss.totalBet.pick}</Text>
                            <Text style={{color:'#7a92a8',fontSize:11}}>{ss.totalBet.odds>0?'+':''}{ss.totalBet.odds}</Text>
                          <Text style={{color:ss.totalBet.book===HRB?HRB_COLOR:'#4a6070',fontSize:9,fontWeight:'700',marginTop:2}}>{ss.totalBet.book===HRB?'🎸 HRB':ss.totalBet.book}</Text>
                          </TouchableOpacity>}
                          {ss.mlBet&&<TouchableOpacity style={{flex:1,backgroundColor:'rgba(255,184,0,0.1)',borderRadius:10,padding:8,alignItems:'center',borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}} onPress={()=>{setForm({matchup:selectedGame.away_team+' vs '+selectedGame.home_team,pick:ss.mlBet.pick,sport:gamesSport,type:'Moneyline',odds:String(ss.mlBet.odds),units:'',book:HRB,result:'Pending'});setGameDetailModal(false);setModalVisible(true);}}>
                            <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>ML</Text>
                            <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:13,marginTop:3}}>{ss.mlBet.pick}</Text>
                            <Text style={{color:'#7a92a8',fontSize:11}}>{ss.mlBet.odds>0?'+':''}{ss.mlBet.odds}</Text>
                           <Text style={{color:ss.mlBet.book===HRB?HRB_COLOR:'#4a6070',fontSize:9,fontWeight:'700',marginTop:2}}>{ss.mlBet.book===HRB?'🎸 HRB':ss.mlBet.book}</Text>
                          </TouchableOpacity>}
                        </View>
                      </View>
                      {/* Score breakdown */}
                      <TouchableOpacity onPress={()=>setExpandedSweatScore(isExpanded?null:'main')} style={{flexDirection:'row',alignItems:'center',gap:4,marginBottom:isExpanded?10:0}}>
                        <Text style={{color:'#4a6070',fontSize:11}}>{isExpanded?'▲ Hide':'▼ Show'} score breakdown</Text>
                      </TouchableOpacity>
                      {isExpanded&&(
                        <View style={{gap:6}}>
                          {[
                            {label:'📊 Market Efficiency',val:ss.marketEfficiency,weight:'20%'},
                            {label:'🔬 Model Mismatch',val:ss.modelMismatch,weight:'25%'},
                            {label:'📈 Line Trajectory',val:ss.lineTrajectory,weight:'20%'},
                            {label:'🔪 Sharp Signal',val:ss.sharpSignal,weight:'20%'},
                            {label:'🎯 Situational Edge',val:ss.situationalEdge,weight:'15%'},
                          ].map((row,j)=>(
                            <View key={j} style={{flexDirection:'row',alignItems:'center',gap:8}}>
                              <Text style={{flex:2.5,color:'#7a92a8',fontSize:11}}>{row.label}</Text>
                              <Text style={{color:'#4a6070',fontSize:10,width:28,textAlign:'right'}}>{row.weight}</Text>
                              <View style={{flex:2,height:6,backgroundColor:'#1f2d3d',borderRadius:3,overflow:'hidden'}}>
                                <View style={{height:'100%',width:row.val+'%',backgroundColor:row.val>=70?'#00e5a0':row.val>=40?'#ffd166':'#ff4d6d',borderRadius:3}}/>
                              </View>
                              <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:12,width:28,textAlign:'right'}}>{row.val}</Text>
                            </View>
                          ))}
                          <Text style={{color:'#4a6070',fontSize:10,marginTop:4}}>
                            {gamesSport==='NCAAB'?'📡 Live efficiency data':gamesSport==='NBA'?'📡 BDL team data':'⚡ Market analysis'}
                          </Text>
                        </View>
                      )}
                    </View>
                  );
                })()}
                {gamesSport === 'MLB' && (
  <View style={{backgroundColor:'rgba(255,184,0,0.06)',borderRadius:12,padding:12,marginBottom:12,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
    <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:11,marginBottom:4}}>⚾ MLB DATA NOTE</Text>
    <Text style={{color:'#7a92a8',fontSize:11,lineHeight:17}}>Schedule and situational stats update as the season progresses. Jerry's analysis uses live pitcher Statcast data, park factors, umpire tendencies, and weather — all updated daily.</Text>
  </View>
)}
{renderMatchupView(selectedGame, gamesSport)}
                {(()=>{
                  const hrbLine=getHRBLine(selectedGame);
                  const hrbEV=getHRBEV(selectedGame);
                  if(!hrbLine)return null;
                  return(
                    <View style={{backgroundColor:'rgba(255,184,0,0.07)',borderRadius:14,padding:14,marginBottom:16,borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}>
                      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
                        <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14}}>🎸 YOUR BOOK — HARD ROCK BET</Text>
                        <View style={{backgroundColor:'rgba(255,184,0,0.15)',borderRadius:6,paddingHorizontal:8,paddingVertical:3}}><Text style={{color:HRB_COLOR,fontSize:10,fontWeight:'700'}}>YOUR BOOK</Text></View>
                      </View>
                      <View style={{flexDirection:'row',gap:8,marginBottom:10}}>
                        {hrbLine.spread&&hrbLine.spread[0]&&gamesSport!=='UFC'&&<View style={{flex:1,backgroundColor:'#151c24',borderRadius:10,padding:10,alignItems:'center'}}><Text style={{color:'#4a6070',fontSize:10,fontWeight:'700'}}>SPREAD</Text><Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:16,marginTop:4}}>{hrbLine.spread[0].name.split(' ').pop()} {hrbLine.spread[0].point>0?'+':''}{hrbLine.spread[0].point}</Text><Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>{hrbLine.spread[0].price>0?'+':''}{hrbLine.spread[0].price}</Text></View>}
                        {hrbLine.total&&hrbLine.total[0]&&<View style={{flex:1,backgroundColor:'#151c24',borderRadius:10,padding:10,alignItems:'center'}}><Text style={{color:'#4a6070',fontSize:10,fontWeight:'700'}}>TOTAL</Text><Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:16,marginTop:4}}>O/U {hrbLine.total[0].point}</Text><Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>{hrbLine.total[0].price>0?'+':''}{hrbLine.total[0].price}</Text></View>}
                        {hrbLine.ml&&hrbLine.ml[0]&&<View style={{flex:1,backgroundColor:'#151c24',borderRadius:10,padding:10,alignItems:'center'}}><Text style={{color:'#4a6070',fontSize:10,fontWeight:'700'}}>ML</Text><Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:16,marginTop:4}}>{hrbLine.ml[0].price>0?'+':''}{hrbLine.ml[0].price}</Text><Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>{selectedGame.away_team.split(' ').pop()}</Text></View>}
                      </View>
                      {hrbEV&&hrbEV.length>0&&(
                        <View>
                          {hrbEV.filter(e=>e.isPositive).slice(0,2).map((e,i)=>(
                            <View key={i} style={{flexDirection:'row',alignItems:'center',gap:8,backgroundColor:'rgba(0,229,160,0.07)',borderRadius:8,padding:8,marginBottom:4}}>
                              <Text style={{color:'#00e5a0',fontSize:12}}>✅</Text>
                              <Text style={{color:'#e8f0f8',fontSize:12,flex:1}}>{e.pick} — <Text style={{color:'#00e5a0',fontWeight:'700'}}>+{e.ev.toFixed(1)}% EV</Text> on Hard Rock</Text>
                            </View>
                          ))}
                          {hrbEV.filter(e=>!e.isPositive).slice(0,1).map((e,i)=>(
                            <View key={i} style={{flexDirection:'row',alignItems:'center',gap:8,backgroundColor:'rgba(255,77,109,0.07)',borderRadius:8,padding:8,marginBottom:4}}>
                              <Text style={{color:'#ff4d6d',fontSize:12}}>⚠️</Text>
                              <Text style={{color:'#7a92a8',fontSize:12,flex:1}}>{e.pick} — <Text style={{color:'#ff4d6d',fontWeight:'700'}}>{e.ev.toFixed(1)}% EV</Text> — shop other books</Text>
                            </View>
                          ))}
                        </View>
                      )}
                    </View>
                  );
                })()}
                {renderLineMovement(selectedGame, historicalOdds, historicalOddsLoading)}
                {(()=>{
  const key = selectedGame.id || (selectedGame.away_team + selectedGame.home_team);
  const alt = altLines[key];
  if(!alt && !altLinesLoading[key]) return null;
  if(altLinesLoading[key]) return(
    <View style={{alignItems:'center',paddingVertical:12}}>
      <ActivityIndicator size="small" color={HRB_COLOR}/>
      <Text style={{color:'#4a6070',fontSize:11,marginTop:4}}>Loading alt lines...</Text>
    </View>
  );
  if(!alt || (!alt.altSpreads.length && !alt.altTotals.length)) return null;
  return(
    <View style={{marginBottom:16}}>
      <Text style={styles.sectionLabel}>ALT LINES</Text>
      {alt.altSpreads.length > 0 && (
        <View style={[styles.card,{marginBottom:10}]}>
          <Text style={{color:'#7a92a8',fontSize:11,fontWeight:'700',marginBottom:8}}>ALTERNATE SPREADS</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <View style={{flexDirection:'row',gap:8}}>
             {alt.altSpreads.filter(s => s.name === selectedGame.away_team || s.name === selectedGame.home_team).reduce((acc, s) => {
                const existing = acc.find(a => a.point === s.point && a.name === s.name);
                if(!existing) acc.push(s);
                else if(s.odds > existing.odds) acc[acc.indexOf(existing)] = s;
                return acc;
              }, []).filter(s => Math.abs(s.odds) <= 350).slice(0,10).map((s,i) => (
                <TouchableOpacity key={i} 
                  onPress={()=>{setForm({matchup:selectedGame.away_team+' vs '+selectedGame.home_team,pick:s.name+' '+( s.point>0?'+':'')+s.point,sport:gamesSport,type:'Spread',odds:String(s.odds),units:'',book:s.book,result:'Pending'});setGameDetailModal(false);setModalVisible(true);}}
                  style={{backgroundColor:s.isHRB?'rgba(255,184,0,0.1)':'#151c24',borderRadius:10,padding:10,alignItems:'center',borderWidth:1,borderColor:s.isHRB?HRB_COLOR:'#1f2d3d',minWidth:80}}>
                  <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>{s.name.split(' ').pop()}</Text>
                  <Text style={{color:s.isHRB?HRB_COLOR:'#e8f0f8',fontWeight:'800',fontSize:14,marginTop:2}}>{s.point>0?'+':''}{s.point}</Text>
                  <Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>{s.odds>0?'+':''}{s.odds}</Text>
                  {s.isHRB&&<Text style={{color:HRB_COLOR,fontSize:9,marginTop:2}}>🎸</Text>}
                </TouchableOpacity>
              ))}
            </View>
          </ScrollView>
        </View>
      )}
      {alt.altTotals.length > 0 && (
        <View style={[styles.card,{marginBottom:10}]}>
          <Text style={{color:'#7a92a8',fontSize:11,fontWeight:'700',marginBottom:8}}>ALTERNATE TOTALS</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <View style={{flexDirection:'row',gap:8}}>
              {alt.altTotals.filter(t => t.name === 'Over').reduce((acc, t) => {
                const existing = acc.find(a => a.point === t.point);
                if(!existing) acc.push(t);
                else if(t.odds > existing.odds) acc[acc.indexOf(existing)] = t;
                return acc;
              }, []).filter(t => Math.abs(t.odds) <= 350).slice(0,10).map((t,i) => (
                <TouchableOpacity key={i}
                  onPress={()=>{setForm({matchup:selectedGame.away_team+' vs '+selectedGame.home_team,pick:'Over '+t.point,sport:gamesSport,type:'Total (O/U)',odds:String(t.odds),units:'',book:t.book,result:'Pending'});setGameDetailModal(false);setModalVisible(true);}}
                  style={{backgroundColor:t.isHRB?'rgba(255,184,0,0.1)':'#151c24',borderRadius:10,padding:10,alignItems:'center',borderWidth:1,borderColor:t.isHRB?HRB_COLOR:'#1f2d3d',minWidth:70}}>
                  <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>O {t.point}</Text>
                  <Text style={{color:t.isHRB?HRB_COLOR:'#e8f0f8',fontWeight:'800',fontSize:14,marginTop:2}}>{t.odds>0?'+':''}{t.odds}</Text>
                  {t.isHRB&&<Text style={{color:HRB_COLOR,fontSize:9,marginTop:2}}>🎸</Text>}
                </TouchableOpacity>
              ))}
            </View>
          </ScrollView>
        </View>
      )}
      {alt.altTotals.filter(t => t.isF5).length > 0 && gamesSport === 'MLB' && (
  <View style={[styles.card,{marginBottom:10}]}>
    <Text style={{color:'#7a92a8',fontSize:11,fontWeight:'700',marginBottom:8}}>FIRST 5 INNINGS</Text>
    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
      <View style={{flexDirection:'row',gap:8}}>
        {alt.altTotals.filter(t => t.isF5).reduce((acc, t) => {
          const existing = acc.find(a => a.point === t.point && a.name === t.name);
          if(!existing) acc.push(t);
          else if(t.odds > existing.odds) acc[acc.indexOf(existing)] = t;
          return acc;
        }, []).filter(t => Math.abs(t.odds) <= 350).slice(0,8).map((t,i) => (
          <TouchableOpacity key={i}
            onPress={()=>{setForm({matchup:selectedGame.away_team+' vs '+selectedGame.home_team,pick:'F5 '+t.name+' '+t.point,sport:gamesSport,type:'Total (O/U)',odds:String(t.odds),units:'',book:t.book,result:'Pending'});setGameDetailModal(false);setModalVisible(true);}}
            style={{backgroundColor:t.isHRB?'rgba(255,184,0,0.1)':'#151c24',borderRadius:10,padding:10,alignItems:'center',borderWidth:1,borderColor:t.isHRB?HRB_COLOR:'#1f2d3d',minWidth:80}}>
            <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>F5 {t.name}</Text>
            <Text style={{color:t.isHRB?HRB_COLOR:'#e8f0f8',fontWeight:'800',fontSize:14,marginTop:2}}>{t.point}</Text>
            <Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>{t.odds>0?'+':''}{t.odds}</Text>
            {t.isHRB&&<Text style={{color:HRB_COLOR,fontSize:9,marginTop:2}}>🎸</Text>}
          </TouchableOpacity>
        ))}
      </View>
    </ScrollView>
  </View>
)}
    </View>
  );
})()}
{gamesSport === 'MLB' && (()=>{
  const mlbCtx = mlbGameContext[selectedGame?.home_team] ||
    mlbGameContext[selectedGame?.away_team] ||
    mlbGameContext[selectedGame?.home_team?.trim()] ||
    mlbGameContext[selectedGame?.away_team?.trim()] ||
    Object.values(mlbGameContext).find((ctx: any) =>
      ctx.home_team === selectedGame?.home_team ||
      ctx.away_team === selectedGame?.away_team ||
      ctx.home_team === selectedGame?.away_team ||
      ctx.away_team === selectedGame?.home_team
    );
  //console.log('mlbGameContext keys:', Object.keys(mlbGameContext).slice(0,5));
  //console.log('NRFI check - sport:', gamesSport, 'mlbCtx:', mlbCtx ? 'FOUND' : 'NOT FOUND', 'home:', selectedGame?.home_team);
  if(!mlbCtx) return(
    <View style={[styles.card,{marginBottom:10}]}>
      <Text style={{color:'#7a92a8',fontSize:11}}>NRFI — awaiting game context data</Text>
    </View>
  );
  // Use pipeline NRFI score if available, fall back to client-side calc
const pipelineNRFI = mlbCtx.nrfi_score;
let nrfiScore;

if(pipelineNRFI !== null && pipelineNRFI !== undefined) {
  // Use server-side score — full model with all inputs
  nrfiScore = pipelineNRFI;
} else {
  // Fall back to client-side calc until pipeline score is available
  const pitcherCtxStr = mlbCtx.pitcher_context || '';
  const homeCtx = pitcherCtxStr.split('|')[0] || '';
  const awayCtx = pitcherCtxStr.split('|')[1] || '';
  const homeKRate = parseFloat(homeCtx.match(/K% ([\d.]+)/)?.[1] || 0);
  const awayKRate = parseFloat(awayCtx.match(/K% ([\d.]+)/)?.[1] || 0);
  const homeWhiff = parseFloat(homeCtx.match(/whiff ([\d.]+)/)?.[1] || 0);
  const awayWhiff = parseFloat(awayCtx.match(/whiff ([\d.]+)/)?.[1] || 0);
  const avgKRate = homeKRate && awayKRate ? (homeKRate + awayKRate) / 2 : homeKRate || awayKRate;
  const avgWhiff = homeWhiff && awayWhiff ? (homeWhiff + awayWhiff) / 2 : homeWhiff || awayWhiff;
  const parkFactor = mlbCtx.park_run_factor || 100;
  const umpireK = mlbCtx.umpire_note?.includes('K-friendly') ? 1 : mlbCtx.umpire_note?.includes('hitter-friendly') ? -1 : 0;
  const windDir = mlbCtx.wind_direction || '';
  const weatherPenalty = mlbCtx.wind_speed > 10
    ? (['S','SW','SE'].includes(windDir) ? -1 : ['N','NW','NE'].includes(windDir) ? 1 : 0)
    : 0;
  const homeGB = parseFloat(homeCtx.match(/GB% ([\d.]+)/)?.[1] || 0);
  const awayGB = parseFloat(awayCtx.match(/GB% ([\d.]+)/)?.[1] || 0);
  const avgGB = homeGB && awayGB ? (homeGB + awayGB) / 2 : homeGB || awayGB;
  const gbBonus = avgGB > 50 ? 3 : avgGB > 45 ? 1 : 0;
  const restBonus = (mlbCtx.home_days_rest >= 5 ? 2 : mlbCtx.home_days_rest <= 3 ? -2 : 0)
                  + (mlbCtx.away_days_rest >= 5 ? 2 : mlbCtx.away_days_rest <= 3 ? -2 : 0);
  const tempBonus = !mlbCtx.temperature ? 0 :
                    mlbCtx.temperature < 45 ? 4 :
                    mlbCtx.temperature < 55 ? 2 :
                    mlbCtx.temperature > 85 ? -2 : 0;
  const kDelta = avgKRate - 22;
  nrfiScore = Math.round(
    50 + (kDelta * 0.6) + (avgWhiff * 0.25) +
    ((100 - parkFactor) * 0.3) + (umpireK * 8) +
    (weatherPenalty * 5) + gbBonus + restBonus + tempBonus
  );
}

// Audit-calibrated NRFI tiers (235 games):
// 90-94: 73.3% (PRIME) | 95+: 47% (volatile) | 70-79: ~60% (mild lean)
// 80-89: 42.5% (NEUTRAL — no edge) | <=40: 77.8% YRFI hit
const nrfiLean  = nrfiScore >= 95 ? 'NRFI (volatile)' : nrfiScore >= 90 ? 'PRIME NRFI' : nrfiScore >= 80 && nrfiScore <= 89 ? 'NEUTRAL' : nrfiScore >= 70 ? 'NRFI lean' : nrfiScore <= 35 ? 'YRFI' : nrfiScore <= 40 ? 'YRFI lean' : 'NEUTRAL';
const nrfiColor = nrfiScore >= 90 && nrfiScore <= 94 ? '#00e5a0' : nrfiScore >= 95 ? '#ffb800' : nrfiScore >= 80 && nrfiScore <= 89 ? '#7a92a8' : nrfiScore >= 70 ? '#4a9eff' : nrfiScore <= 40 ? '#ff4d6d' : '#7a92a8';
  return(
    <View style={[styles.card,{marginBottom:10}]}>
      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
        <Text style={{color:'#7a92a8',fontSize:11,fontWeight:'700'}}>NRFI / YRFI SIGNAL</Text>
        <View style={{backgroundColor:`${nrfiColor}20`,borderRadius:6,paddingHorizontal:8,paddingVertical:3}}>
          <Text style={{color:nrfiColor,fontSize:11,fontWeight:'800'}}>{nrfiLean}</Text>
        </View>
      </View>
      <View style={{flexDirection:'row',gap:8}}>
        <View style={{flex:1,backgroundColor:'#151c24',borderRadius:8,padding:8,alignItems:'center'}}>
          <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>NRFI SCORE</Text>
          <Text style={{color:nrfiColor,fontWeight:'800',fontSize:20,marginTop:2}}>{Math.min(99,Math.max(1,nrfiScore))}</Text>
        </View>
        <View style={{flex:2,backgroundColor:'#151c24',borderRadius:8,padding:8}}>
          <Text style={{color:'#4a6070',fontSize:9,fontWeight:'700',marginBottom:4}}>KEY SIGNALS</Text>
          {(()=>{
            const homeXERA = mlbCtx.home_sp_xera && parseFloat(mlbCtx.home_sp_xera) <= 6.5
              ? parseFloat(mlbCtx.home_sp_xera).toFixed(2)
              : null;
            const awayXERA = mlbCtx.away_sp_xera && parseFloat(mlbCtx.away_sp_xera) <= 6.5
              ? parseFloat(mlbCtx.away_sp_xera).toFixed(2)
              : null;
            return(<>
              {(mlbCtx.home_pitcher || selectedGame?.home_pitcher) && <Text style={{color:'#e8f0f8',fontSize:10}}>🏠 {mlbCtx.home_pitcher || selectedGame?.home_pitcher} xERA: {homeXERA || 'N/A'}</Text>}
              {(mlbCtx.away_pitcher || selectedGame?.away_pitcher) && <Text style={{color:'#e8f0f8',fontSize:10}}>✈️ {mlbCtx.away_pitcher || selectedGame?.away_pitcher} xERA: {awayXERA || 'N/A'}</Text>}
            </>);
          })()}
          {mlbCtx.umpire_note ? <Text style={{color:'#e8f0f8',fontSize:10}}>{mlbCtx.umpire_note.includes('K-friendly') ? '✅ K-friendly ump' : mlbCtx.umpire_note.includes('hitter') ? '⚠️ Hitter-friendly ump' : `Ump: ${mlbCtx.umpire || 'TBD'}`}</Text> : null}
          <Text style={{color:'#e8f0f8',fontSize:10}}>Park: {mlbCtx.park_run_factor} {mlbCtx.park_run_factor >= 110 ? '⚠️ hitter' : mlbCtx.park_run_factor <= 93 ? '✅ pitcher' : '—'} | {mlbCtx.temperature}°F</Text>
          {pipelineNRFI !== null && pipelineNRFI !== undefined && <Text style={{color:'#4a6070',fontSize:9,marginTop:2}}>Pipeline model ✓</Text>}
        </View>
      </View>
    </View>
  );
})()}
                <Text style={styles.sectionLabel}>LOG A PICK</Text>
                <View style={{flexDirection:'row',gap:6,marginBottom:16,flexWrap:'wrap'}}>
                  {[
                    {label:selectedGame.away_team.split(' ').pop()+' Spread',pick:selectedGame.away_team+' spread'},
                    {label:selectedGame.home_team.split(' ').pop()+' Spread',pick:selectedGame.home_team+' spread'},
                    {label:'Over',pick:'Over '+getGameSummary(selectedGame).total},
                    {label:'Under',pick:'Under '+getGameSummary(selectedGame).total},
                    {label:selectedGame.away_team.split(' ').pop()+' ML',pick:selectedGame.away_team+' ML'},
                    {label:selectedGame.home_team.split(' ').pop()+' ML',pick:selectedGame.home_team+' ML'},
                  ].map((opt,i)=>(
                    <TouchableOpacity key={i} style={[styles.chipBtn,{marginBottom:4}]} onPress={()=>logPickFromGame(selectedGame,opt.pick)}>
                      <Text style={styles.chipTxt}>📋 {opt.label}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
                <Text style={styles.sectionLabel}>ALL BOOK LINES — TAP TO ADD PARLAY LEG</Text>
                {selectedGame.bookmakers&&[...selectedGame.bookmakers].sort((a,b)=>{
                  const aIsHRB=(BOOKMAKER_MAP[a.key]||a.key)===HRB;
                  const bIsHRB=(BOOKMAKER_MAP[b.key]||b.key)===HRB;
                  if(aIsHRB)return -1; if(bIsHRB)return 1; return 0;
                }).map((bm,i)=>{
                  const spreadMkt=bm.markets&&bm.markets.find(m=>m.key==='spreads');
                  const totalMkt=bm.markets&&bm.markets.find(m=>m.key==='totals');
                  const mlMkt=bm.markets&&bm.markets.find(m=>m.key==='h2h');
                  const bookName=BOOKMAKER_MAP[bm.key]||bm.key;
                  const isHRB=bookName===HRB;
                  return(
                    <View key={i} style={[styles.card,{marginBottom:8,padding:12,borderColor:isHRB?'rgba(255,184,0,0.4)':'#1f2d3d',borderWidth:isHRB?1.5:1}]}>
                      <View style={{flexDirection:'row',alignItems:'center',gap:8,marginBottom:8}}>
                        <Text style={{color:isHRB?HRB_COLOR:'#00e5a0',fontWeight:'700',fontSize:12}}>{isHRB?'🎸 HARD ROCK BET':bookName}</Text>
                        {isHRB&&<View style={{backgroundColor:'rgba(255,184,0,0.15)',borderRadius:6,paddingHorizontal:6,paddingVertical:2}}><Text style={{color:HRB_COLOR,fontSize:9,fontWeight:'800'}}>YOUR BOOK</Text></View>}
                      </View>
                      {spreadMkt&&spreadMkt.outcomes&&gamesSport!=='UFC'&&spreadMkt.outcomes.map((outcome,j)=>(
                        <TouchableOpacity key={j} style={styles.parlayLineRow} onPress={()=>addToParlay(selectedGame,outcome.name+' '+(outcome.point>0?'+':'')+outcome.point,outcome.price)}>
                          <View style={{flex:1}}><Text style={{color:'#e8f0f8',fontSize:13,fontWeight:'600'}}>{outcome.name} {outcome.point>0?'+':''}{outcome.point}</Text><Text style={{color:'#7a92a8',fontSize:11}}>Spread</Text></View>
                          <Text style={{color:isHRB?HRB_COLOR:'#e8f0f8',fontWeight:'700',marginRight:12}}>{outcome.price>0?'+':''}{outcome.price}</Text>
                          <View style={[styles.addParlayBtn,isHRB&&{borderColor:HRB_COLOR,backgroundColor:'rgba(255,184,0,0.12)'}]}><Text style={[styles.addParlayBtnText,isHRB&&{color:HRB_COLOR}]}>+ Parlay</Text></View>
                        </TouchableOpacity>
                      ))}
                      {totalMkt&&totalMkt.outcomes&&totalMkt.outcomes.map((outcome,j)=>(
                        <TouchableOpacity key={'t'+j} style={styles.parlayLineRow} onPress={()=>addToParlay(selectedGame,outcome.name+' '+(totalMkt.outcomes[0]&&totalMkt.outcomes[0].point),outcome.price)}>
                          <View style={{flex:1}}><Text style={{color:'#e8f0f8',fontSize:13,fontWeight:'600'}}>{outcome.name} {totalMkt.outcomes[0]&&totalMkt.outcomes[0].point}</Text><Text style={{color:'#7a92a8',fontSize:11}}>Total</Text></View>
                          <Text style={{color:isHRB?HRB_COLOR:'#e8f0f8',fontWeight:'700',marginRight:12}}>{outcome.price>0?'+':''}{outcome.price}</Text>
                          <View style={[styles.addParlayBtn,isHRB&&{borderColor:HRB_COLOR,backgroundColor:'rgba(255,184,0,0.12)'}]}><Text style={[styles.addParlayBtnText,isHRB&&{color:HRB_COLOR}]}>+ Parlay</Text></View>
                        </TouchableOpacity>
                      ))}
                      {mlMkt&&mlMkt.outcomes&&mlMkt.outcomes.map((outcome,j)=>(
                        <TouchableOpacity key={'ml'+j} style={[styles.parlayLineRow,{borderBottomWidth:0}]} onPress={()=>addToParlay(selectedGame,outcome.name+' ML',outcome.price)}>
                          <View style={{flex:1}}><Text style={{color:'#e8f0f8',fontSize:13,fontWeight:'600'}}>{outcome.name}</Text><Text style={{color:'#7a92a8',fontSize:11}}>Moneyline</Text></View>
                          <Text style={{color:isHRB?HRB_COLOR:'#e8f0f8',fontWeight:'700',marginRight:12}}>{outcome.price>0?'+':''}{outcome.price}</Text>
                          <View style={[styles.addParlayBtn,isHRB&&{borderColor:HRB_COLOR,backgroundColor:'rgba(255,184,0,0.12)'}]}><Text style={[styles.addParlayBtnText,isHRB&&{color:HRB_COLOR}]}>+ Parlay</Text></View>
                        </TouchableOpacity>
                      ))}
                    </View>
                  );
                })}
                {parlayLegs.length>0&&(
                  <TouchableOpacity style={[styles.btnPrimary,{marginTop:8}]} onPress={()=>{setGameDetailModal(false);setActiveTab('parlay');}}>
                    <Text style={styles.btnPrimaryText}>View Parlay ({parlayLegs.length} legs) 🎰</Text>
                  </TouchableOpacity>
                )}
                <View style={{height:20}}/>
              </ScrollView>
              <TouchableOpacity style={[styles.btnPrimary,{backgroundColor:'transparent',borderWidth:1,borderColor:'#1f2d3d',marginTop:8}]} onPress={()=>setGameDetailModal(false)}>
                <Text style={[styles.btnPrimaryText,{color:'#7a92a8'}]}>Close</Text>
              </TouchableOpacity>
            </View>
          </View>
        </Modal>
      )}

      <Modal visible={unitSizeModal} animationType="slide" transparent>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{flex:1,justifyContent:'flex-end',backgroundColor:'rgba(0,0,0,0.6)'}}>
          <View style={[styles.modalSheet]}>
            <View style={styles.modalHandle}/>
            <Text style={styles.modalTitle}>Set Unit Size</Text>
            <Text style={{color:'#7a92a8',fontSize:14,marginBottom:20,marginTop:-8}}>How much is 1 unit worth in dollars?</Text>
            <View style={{flexDirection:'row',alignItems:'center',backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',borderRadius:12,paddingHorizontal:14,paddingVertical:10,marginBottom:16}}>
              <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:18,marginRight:8}}>$</Text>
              <TextInput style={{flex:1,color:'#e8f0f8',fontSize:20,fontWeight:'700'}} placeholder="25" placeholderTextColor="#4a6070" value={tempUnitSize} onChangeText={setTempUnitSize} keyboardType="numeric" returnKeyType="done" autoFocus/>
              <Text style={{color:'#7a92a8',fontSize:13}}>per unit</Text>
            </View>
            <View style={{flexDirection:'row',gap:8}}>
              {['10','25','50','100'].map(v=>(<TouchableOpacity key={v} style={[styles.chipBtn,tempUnitSize===v&&styles.chipBtnActive,{flex:1,alignItems:'center'}]} onPress={()=>setTempUnitSize(v)}><Text style={[styles.chipTxt,tempUnitSize===v&&styles.chipTxtActive]}>${v}</Text></TouchableOpacity>))}
            </View>
            <View style={{height:16}}/>
            <TouchableOpacity style={styles.btnPrimary} onPress={saveUnitSize}><Text style={styles.btnPrimaryText}>Save Unit Size ✓</Text></TouchableOpacity>
            <TouchableOpacity style={[styles.btnPrimary,{backgroundColor:'transparent',borderWidth:1,borderColor:'#1f2d3d',marginTop:8}]} onPress={()=>setUnitSizeModal(false)}><Text style={[styles.btnPrimaryText,{color:'#7a92a8'}]}>Cancel</Text></TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      </Modal>

{/* SETTINGS MODAL */}
      <Modal visible={settingsModal} transparent animationType="slide" onRequestClose={()=>setSettingsModal(false)}>
        <View style={{flex:1,backgroundColor:'rgba(0,0,0,0.85)',justifyContent:'flex-end'}}>
          <View style={{backgroundColor:'#0d1821',borderTopLeftRadius:24,borderTopRightRadius:24,padding:20,maxHeight:'90%'}}>
            <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:20}}>
              <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:20}}>⚙️ Settings</Text>
              <TouchableOpacity onPress={()=>setSettingsModal(false)} style={{padding:8}}>
                <Text style={{color:'#7a92a8',fontSize:18}}>✕</Text>
              </TouchableOpacity>
            </View>
            <ScrollView showsVerticalScrollIndicator={false}>
              {/* Unit Size */}
              <View style={[styles.card,{marginBottom:12}]}>
                <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14,marginBottom:12}}>💰 Unit Size</Text>
                <View style={{flexDirection:'row',gap:8,flexWrap:'wrap'}}>
                  {[10,25,50,100,250].map(u=>(
                    <TouchableOpacity key={u} style={{paddingHorizontal:16,paddingVertical:8,borderRadius:10,borderWidth:1,borderColor:unitSize===u?HRB_COLOR:'#1f2d3d',backgroundColor:unitSize===u?'rgba(255,184,0,0.1)':'#151c24'}} onPress={()=>setUnitSize(u)}>
                      <Text style={{color:unitSize===u?HRB_COLOR:'#7a92a8',fontWeight:'700'}}>${u}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
              {/* How It Works */}
              <View style={[styles.card,{marginBottom:12}]}>
                <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14,marginBottom:12}}>📖 How It Works</Text>
                <View style={{gap:12}}>
                  <View style={{borderLeftWidth:3,borderLeftColor:HRB_COLOR,paddingLeft:10}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:4}}>🔥 Sweat Score</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Every game graded 0-100. Model-heavy for MLB and NBA using real pipeline data. 68+ is Prime Sweat — requires multiple strong signals aligning. Updates at 8am and 2pm ET.</Text>
                  </View>
                  <View style={{borderLeftWidth:3,borderLeftColor:'#00e5a0',paddingLeft:10}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:4}}>⚾ NRFI Model</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>No Run First Inning predictions built on pitcher xERA, K rate matchups, ground ball rate, rest, weather, park factor, and lineup quality. Updates twice daily.</Text>
                  </View>
                  <View style={{borderLeftWidth:3,borderLeftColor:'#0099ff',paddingLeft:10}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:4}}>🎯 Prop Jerry</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>EV scanner across MLB, NBA, NHL, and UFC. A grades require both market edge AND independent model confirmation — BDL last 5 game averages for NBA, K gap and platoon data for MLB. Pitcher K props capped until May 1.</Text>
                  </View>
                  <View style={{borderLeftWidth:3,borderLeftColor:'#ff4d6d',paddingLeft:10}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:4}}>🎰 Daily Degen</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Jerry scans the full slate and builds a 3-4 leg analytically validated parlay daily. Legs must pass Sweat Score validation before inclusion. Loads after 2pm ET with full pipeline data.</Text>
                  </View>
                  <View style={{borderLeftWidth:3,borderLeftColor:'#ff8c00',paddingLeft:10}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:4}}>🚫 Jerry's Fades</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Games to avoid based on sharp consensus, heavy public action, and thin data signals. Updated daily alongside Daily Degen.</Text>
                  </View>
                  <View style={{borderLeftWidth:3,borderLeftColor:'#9b59b6',paddingLeft:10}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:4}}>📊 Jerry's Track Record</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Model performance dashboard. NRFI record, A-grade prop results, and Daily Play of the Day history. Results auto-resolve daily via MLB Stats API and BDL box scores.</Text>
                  </View>
                  <View style={{borderLeftWidth:3,borderLeftColor:'#4a6070',paddingLeft:10}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:4}}>⏰ Pipeline Schedule</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>8am ET — Game context, pitchers, NRFI scores{'\n'}2pm ET — Confirmed lineups, umpires, final weather{'\n'}After 2pm — Prop Jerry most accurate, Daily Degen fully loaded</Text>
                  </View>
                </View>
              </View>
              {/* Responsible Gambling */}
              <View style={[styles.card,{marginBottom:12}]}>
                <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14,marginBottom:12}}>🆘 Responsible Gambling</Text>
                <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18,marginBottom:12}}>If you or someone you know has a gambling problem, free help is available 24/7.</Text>
                <TouchableOpacity style={{flexDirection:'row',alignItems:'center',justifyContent:'space-between',paddingVertical:12,borderTopWidth:1,borderTopColor:'#1f2d3d'}} onPress={()=>Linking.openURL('tel:18004262537')}>
                  <View>
                    <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:14}}>1-800-GAMBLER</Text>
                    <Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>National Problem Gambling Helpline</Text>
                  </View>
                  <Text style={{fontSize:18}}>📞</Text>
                </TouchableOpacity>
                <TouchableOpacity style={{flexDirection:'row',alignItems:'center',justifyContent:'space-between',paddingVertical:12,borderTopWidth:1,borderTopColor:'#1f2d3d'}} onPress={()=>Linking.openURL('tel:18882364848')}>
                  <View>
                    <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:14}}>1-888-ADMIT-IT</Text>
                    <Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>Florida Council on Compulsive Gambling</Text>
                  </View>
                  <Text style={{fontSize:18}}>📞</Text>
                </TouchableOpacity>
              </View>
              {/* Privacy Policy */}
              <TouchableOpacity style={[styles.card,{marginBottom:12}]} onPress={()=>setShowPrivacy(!showPrivacy)} activeOpacity={0.7}>
                <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center'}}>
                  <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14}}>🔒 Privacy Policy</Text>
                  <Text style={{color:'#4a6070',fontSize:12}}>{showPrivacy?'Hide ▲':'View ▼'}</Text>
                </View>
                <Text style={{color:'#7a92a8',fontSize:12,marginTop:8,lineHeight:18}}>Your data stays yours. We never sell or share it with advertisers. Last updated April 2026.</Text>
                {showPrivacy&&(
                <Text style={{color:'#7a92a8',fontSize:12,lineHeight:20,marginTop:12}}>
                  Note: "Hard Rock Bet" and the guitar logo are trademarks of the Seminole Tribe of Florida/Hard Rock Digital. Referenced for informational purposes only.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>WHO WE ARE{'\n'}</Text>
                  The Sweat Locker is an AI powered, model driven, sports analytics application owned and operated by The Sweat Locker LLC, a veteran-owned business. Contact us at: support@thesweatlocker.com{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>DATA WE COLLECT{'\n'}</Text>
                  Data you provide:{'\n'}
                  - Bet logs, picks, and parlay history you manually enter{'\n'}
                  - Sport preferences and unit size settings{'\n'}
                  - Email address if you contact support{'\n\n'}
                  Data collected automatically:{'\n'}
                  - App usage and feature interaction (anonymous analytics only){'\n'}
                  - Subscription status and purchase history (processed by Apple and RevenueCat){'\n'}
                  - Device type and OS version for compatibility purposes{'\n\n'}
                  We do not collect location data, financial information, social security numbers, or government IDs.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>HOW WE USE YOUR DATA{'\n'}</Text>
                  - To display your personal bet history and performance tracking{'\n'}
                  - To deliver personalized analytics based on your sport preferences{'\n'}
                  - To manage your subscription status{'\n'}
                  - To improve app performance and fix bugs{'\n\n'}
                  We never sell your data. We never share your data with advertisers.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>THIRD PARTY SERVICES{'\n'}</Text>
                  The Sweat Locker uses the following third party services to operate:{'\n\n'}
                  Apple App Store — handles all payment processing. We never see or store your card information.{'\n'}
                  RevenueCat — manages subscription status and trial periods. Privacy policy at revenuecat.com/privacy{'\n'}
                  Supabase — secure cloud database for app analytics data. Privacy policy at supabase.com/privacy{'\n'}
                  Anthropic (Claude AI) — powers Jerry AI game analysis. Prompts are not stored or used for training. Privacy policy at anthropic.com/privacy{'\n'}
                  The Odds API — provides live sportsbook odds data{'\n'}
                  Ball Don't Lie (BDL) — provides NBA statistics{'\n'}
                  MLB Stats API — provides MLB statistics{'\n'}
                  Barttorvik — provides NCAAB analytics data{'\n'}
                  Open-Meteo — provides weather data for game context{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>YOUR DATA RIGHTS{'\n'}</Text>
                  - You can delete all manually entered bet history anytime within the app{'\n'}
                  - You can request complete deletion of your account data by emailing support@thesweatlocker.com{'\n'}
                  - Your bet history is stored locally on your device and in your personal account only{'\n'}
                  - We will respond to data deletion requests within 30 days{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>SUBSCRIPTIONS{'\n'}</Text>
                  - Subscriptions are managed through Apple and RevenueCat{'\n'}
                  - You can cancel anytime through your Apple ID settings → Subscriptions{'\n'}
                  - Refunds are handled by Apple per their standard refund policy{'\n'}
                  - A 7-day free trial is available for new subscribers only{'\n'}
                  - Subscription pricing: $9.99/month or $99/year{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>AI-GENERATED CONTENT{'\n'}</Text>
                  Jerry AI analysis is generated by Anthropic's Claude AI model and is intended for entertainment and informational purposes only. AI analysis does not constitute financial or betting advice. Past model performance does not guarantee future results.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>AGE REQUIREMENT{'\n'}</Text>
                  The Sweat Locker is intended for users 18 years of age or older. We do not knowingly collect data from users under 18. If you believe a minor has created an account please contact us immediately.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>DISCLAIMER{'\n'}</Text>
                  The Sweat Locker provides sports analytics for entertainment purposes only. We do not facilitate wagering of any kind. Nothing in this app constitutes betting advice. Please bet responsibly and in accordance with your local laws and regulations.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>CHANGES TO THIS POLICY{'\n'}</Text>
                  We may update this Privacy Policy periodically. Continued use of the app after changes constitutes acceptance of the updated policy. Material changes will be communicated via in-app notification.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>CONTACT{'\n'}</Text>
                  The Sweat Locker LLC{'\n'}
                  support@thesweatlocker.com
                </Text>
                )}
              </TouchableOpacity>
                {/* Terms of Service */}
              <TouchableOpacity style={[styles.card,{marginBottom:12}]} onPress={()=>setShowTerms(!showTerms)} activeOpacity={0.7}>
                <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center'}}>
                  <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14}}>📋 Terms of Service</Text>
                  <Text style={{color:'#4a6070',fontSize:12}}>{showTerms?'Hide ▲':'View ▼'}</Text>
                </View>
                <Text style={{color:'#7a92a8',fontSize:12,marginTop:8,lineHeight:18}}>Entertainment and analytics only. Not gambling advice. Must be 18+. Last updated April 2026.</Text>
                {showTerms&&(
                <Text style={{color:'#7a92a8',fontSize:12,lineHeight:20,marginTop:12}}>
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>1. ACCEPTANCE OF TERMS{'\n'}</Text>
                  By downloading, accessing, or using The Sweat Locker ("App"), you agree to these Terms of Service. If you do not agree, do not use the App.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>2. ELIGIBILITY{'\n'}</Text>
                  You must be at least 18 years of age to use this App. If sports betting is legal in your jurisdiction, you must also meet the minimum legal betting age required there. Sports betting is not legal in all jurisdictions — you are solely responsible for knowing and complying with your local laws. We do not facilitate wagering of any kind.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>3. NATURE OF THE APP{'\n'}</Text>
                  The Sweat Locker is a sports analytics and information tool for entertainment purposes only. Nothing in the App constitutes financial, legal, or gambling advice. Jerry AI analysis is generated by artificial intelligence and reflects model outputs, not professional advice of any kind.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>4. NO GUARANTEE OF RESULTS{'\n'}</Text>
                  The Sweat Score, NRFI Model, Prop Jerry grades, EV calculations, and all analytical outputs are probabilistic in nature and based on historical data patterns. Model performance records displayed in the App reflect past results only. Past performance does not guarantee future results. You may lose money betting on sports regardless of information provided by this App.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>5. SUBSCRIPTIONS AND BILLING{'\n'}</Text>
                  - Subscriptions are billed through Apple and managed via RevenueCat{'\n'}
                  - Your subscription automatically renews unless cancelled at least 24 hours before the end of the current billing period{'\n'}
                  - You can cancel anytime through your Apple ID settings → Subscriptions{'\n'}
                  - The 7-day free trial is available to new subscribers only — one trial per Apple ID{'\n'}
                  - Founding Annual pricing ($79/year) is limited to the first 100 subscribers and may be discontinued at any time{'\n'}
                  - Refunds are handled by Apple per their standard refund policy — we do not process refunds directly{'\n'}
                  - Prices are in USD and subject to change with reasonable notice{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>6. DATA ACCURACY{'\n'}</Text>
                  We source data from The Odds API, MLB Stats API, Ball Don't Lie, Barttorvik, Baseball Savant, and other public sports data providers. We make no warranty that data is complete, accurate, or current at all times. Pipeline data is updated twice daily. Always verify odds and lines with your sportsbook before placing any wager.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>7. AI-GENERATED CONTENT{'\n'}</Text>
                  Jerry AI is powered by Anthropic's Claude AI model. AI-generated analysis may contain errors, omissions, or outdated information. We are not responsible for decisions made based on AI-generated content. Jerry AI analysis is clearly labeled as AI-generated throughout the App.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>8. RESPONSIBLE GAMBLING{'\n'}</Text>
                  We are committed to responsible gambling. If you or someone you know has a gambling problem:{'\n'}
                  National Problem Gambling Helpline: 1-800-522-4700{'\n'}
                  Online chat: ncpgambling.org{'\n'}
                  Text: Text "HOPENY" to 467369{'\n'}
                  Please gamble responsibly and within your means.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>9. PROHIBITED USES{'\n'}</Text>
                  You agree not to:{'\n'}
                  - Scrape, reproduce, or redistribute any data or content from the App{'\n'}
                  - Reverse engineer any part of the App or pipeline{'\n'}
                  - Use the App for any commercial purpose without written permission{'\n'}
                  - Share subscription access with others{'\n'}
                  - Attempt to circumvent the freemium gating system{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>10. THIRD PARTY SERVICES{'\n'}</Text>
                  The App integrates with third party services including Apple, RevenueCat, Supabase, Anthropic, and various sports data providers. Your use of those services is subject to their respective terms and privacy policies. We are not responsible for third party service outages or data errors.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>11. LIMITATION OF LIABILITY{'\n'}</Text>
                  To the fullest extent permitted by law, The Sweat Locker LLC and its developers, officers, and affiliates shall not be liable for any direct, indirect, incidental, or consequential damages arising from your use of the App, including but not limited to gambling losses, decisions made based on App analytics, or service interruptions.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>12. DISCLAIMER OF WARRANTIES{'\n'}</Text>
                  The App is provided "as is" without warranties of any kind, express or implied. We do not warrant that the App will be error-free, uninterrupted, or that data will always be accurate or current.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>13. CHANGES TO TERMS{'\n'}</Text>
                  We reserve the right to update these Terms at any time. Continued use of the App after changes constitutes acceptance of the updated Terms. Material changes will be communicated via in-app notification.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>14. GOVERNING LAW{'\n'}</Text>
                  These Terms are governed by the laws of the State of Florida. Any disputes shall be resolved in the courts of Brevard County, Florida.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>15. CONTACT{'\n'}</Text>
                  The Sweat Locker LLC{'\n'}
                  support@thesweatlocker.com
                </Text>
                )}
              </TouchableOpacity>
              {/* Delete Data */}
              <View style={[styles.card,{marginBottom:12}]}>
                <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14,marginBottom:8}}>🗑️ Delete My Data</Text>
                <Text style={{color:'#7a92a8',fontSize:12,marginBottom:12}}>Permanently delete all your bet logs and app preferences. This cannot be undone.</Text>
                <TouchableOpacity style={{backgroundColor:'rgba(255,77,109,0.1)',borderRadius:10,padding:12,borderWidth:1,borderColor:'rgba(255,77,109,0.3)',alignItems:'center'}} onPress={()=>{
                  Alert.alert('Delete All Data','This will permanently delete all your bets and preferences. Are you sure?',[
                    {text:'Cancel',style:'cancel'},
                    {text:'Delete Everything',style:'destructive',onPress:async()=>{
                      await AsyncStorage.clear();
                      setBets([]);
                      setSettingsModal(false);
                    }}
                  ]);
                }}>
                  <Text style={{color:'#ff4d6d',fontWeight:'700'}}>Delete All My Data</Text>
                </TouchableOpacity>
              </View>
              {/* App Version */}
              <View style={{alignItems:'center',paddingVertical:16}}>
                <Text style={{color:'#4a6070',fontSize:11}}>The Sweat Locker v1.0.0 — Beta</Text>
                <Text style={{color:'#4a6070',fontSize:10,marginTop:4}}>Built for Hard Rock Bettors 🎸</Text>
                <Text style={{color:'#4a6070',fontSize:10,marginTop:4}}>⚠️ For entertainment only. Not gambling advice.</Text>
              </View>
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* PROP HISTORY MODAL */}
      <Modal visible={propHistoryModal} transparent animationType="slide" onRequestClose={()=>setPropHistoryModal(false)}>
        <View style={{flex:1,backgroundColor:'rgba(0,0,0,0.85)',justifyContent:'flex-end'}}>
          <View style={{backgroundColor:'#0d1821',borderTopLeftRadius:24,borderTopRightRadius:24,padding:20,maxHeight:'85%'}}>
            {selectedPropPlayer&&(
              <>
                <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
                  <View>
                    <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:18}}>{selectedPropPlayer.name}</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,marginTop:2}}>{selectedPropPlayer.team} • {selectedPropPlayer.position}</Text>
                  </View>
                  <TouchableOpacity onPress={()=>setPropHistoryModal(false)} style={{padding:8}}>
                    <Text style={{color:'#7a92a8',fontSize:18}}>✕</Text>
                  </TouchableOpacity>
                </View>
                {/* Stat selector */}
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:12}}>
                  <View style={{flexDirection:'row',gap:6}}>
                    {[
                      {id:'pts',label:'PTS',line:selectedPropPlayer.line},
                      {id:'reb',label:'REB',line:null},
                      {id:'ast',label:'AST',line:null},
                      {id:'stl',label:'STL',line:null},
                      {id:'blk',label:'BLK',line:null},
                    ].map(s=>(
                      <TouchableOpacity key={s.id} style={{paddingHorizontal:14,paddingVertical:7,borderRadius:10,borderWidth:1,borderColor:propHistoryStat===s.id?HRB_COLOR:'#1f2d3d',backgroundColor:propHistoryStat===s.id?'rgba(255,184,0,0.1)':'#151c24'}} onPress={()=>{setPropHistoryStat(s.id);fetchPropHistory(selectedPropPlayer,s.id);}}>
                        <Text style={{color:propHistoryStat===s.id?HRB_COLOR:'#7a92a8',fontWeight:'700',fontSize:12}}>{s.label}{s.line?` (${s.line})`:''}  </Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                </ScrollView>
                {/* Range + Chart tab toggle */}
                <View style={{flexDirection:'row',justifyContent:'space-between',marginBottom:12}}>
                  <View style={{flexDirection:'row',gap:4}}>
                    {[{id:'last5',label:'L5'},{id:'last10',label:'L10'}].map(r=>(
                      <TouchableOpacity key={r.id} style={{paddingHorizontal:12,paddingVertical:5,borderRadius:8,borderWidth:1,borderColor:propHistoryRange===r.id?HRB_COLOR:'#1f2d3d',backgroundColor:propHistoryRange===r.id?'rgba(255,184,0,0.1)':'transparent'}} onPress={()=>setPropHistoryRange(r.id)}>
                        <Text style={{color:propHistoryRange===r.id?HRB_COLOR:'#7a92a8',fontSize:11,fontWeight:'700'}}>{r.label}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                  <View style={{flexDirection:'row',gap:4}}>
                    {[{id:'bars',label:'📊'},{id:'trend',label:'📈'}].map(t=>(
                      <TouchableOpacity key={t.id} style={{paddingHorizontal:12,paddingVertical:5,borderRadius:8,borderWidth:1,borderColor:propHistoryTab===t.id?HRB_COLOR:'#1f2d3d',backgroundColor:propHistoryTab===t.id?'rgba(255,184,0,0.1)':'transparent'}} onPress={()=>setPropHistoryTab(t.id)}>
                        <Text style={{color:propHistoryTab===t.id?HRB_COLOR:'#7a92a8',fontSize:13,fontWeight:'700'}}>{t.label}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                </View>
                {propHistoryLoading?(
                  <View style={{alignItems:'center',paddingVertical:40}}>
                    <Text style={{color:'#7a92a8',fontSize:13}}>Loading game log...</Text>
                  </View>
                ):(()=>{
                  const rangeData = propHistoryRange==='last5' ? propHistoryData.slice(0,5) : propHistoryData;
                  if(rangeData.length===0) return(
                    <View style={{alignItems:'center',paddingVertical:40}}>
                      <Text style={{fontSize:28}}>📊</Text>
                      <Text style={{color:'#7a92a8',fontSize:12,marginTop:8}}>No game log available</Text>
                    </View>
                  );
                  const line = selectedPropPlayer.line || 0;
                  const vals = rangeData.map(g=>g[propHistoryStat]||0);
                  const maxVal = Math.max(...vals, line+5);
                  const avg = vals.length ? (vals.reduce((a,b)=>a+b,0)/vals.length).toFixed(1) : 0;
                  const hits = vals.filter(v=>v>line).length;
                  const hitRate = vals.length ? Math.round((hits/vals.length)*100) : 0;
                  const chartH = 140;
                  const chartW = 300;
                  return(
                    <View>
                      {/* Stats summary */}
                      <View style={{flexDirection:'row',justifyContent:'space-around',marginBottom:16,backgroundColor:'#151c24',borderRadius:12,padding:12}}>
                        <View style={{alignItems:'center'}}>
                          <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:18}}>{avg}</Text>
                          <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>AVG</Text>
                        </View>
                        <View style={{alignItems:'center'}}>
                          <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:18}}>{line||'—'}</Text>
                          <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>LINE</Text>
                        </View>
                        <View style={{alignItems:'center'}}>
                          <Text style={{color:hitRate>=55?'#00e5a0':hitRate>=45?'#ffd166':'#ff4d6d',fontWeight:'800',fontSize:18}}>{hitRate}%</Text>
                          <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>HIT RATE</Text>
                        </View>
                        <View style={{alignItems:'center'}}>
                          <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:18}}>{hits}/{vals.length}</Text>
                          <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>OVERS</Text>
                        </View>
                      </View>
                      {propHistoryTab==='bars'&&(
                        <Svg width={chartW} height={chartH+24} style={{alignSelf:'center'}}>
                          {/* Prop line */}
                          {line>0&&(()=>{
                            const lineY = chartH - (line/maxVal*chartH);
                            return(
                              <>
                                <SvgLine x1={0} y1={lineY} x2={chartW} y2={lineY} stroke="#ff4d6d" strokeWidth={1.5} strokeDasharray="5,3"/>
                                <SvgText x={chartW-28} y={lineY-3} fill="#ff4d6d" fontSize={9} fontWeight="bold">{line}</SvgText>
                              </>
                            );
                          })()}
                          {/* Bars */}
                          {rangeData.map((g,i)=>{
                            const barW = (chartW/rangeData.length)*0.6;
                            const spacing = chartW/rangeData.length;
                            const x = i*spacing + spacing*0.2;
                            const val = g[propHistoryStat]||0;
                            const barH = Math.max(2, (val/maxVal)*chartH);
                            const y = chartH - barH;
                            const isOver = val > line;
                            return(
                              <G key={i}>
                                <Rect x={x} y={y} width={barW} height={barH} fill={isOver?'rgba(0,229,160,0.8)':'rgba(255,77,109,0.8)'} rx={3}/>
                                <SvgText x={x+barW/2} y={y-3} fill="#e8f0f8" fontSize={9} textAnchor="middle" fontWeight="bold">{val}</SvgText>
                                <SvgText x={x+barW/2} y={chartH+14} fill="#7a92a8" fontSize={8} textAnchor="middle">{g.date}</SvgText>
                              </G>
                            );
                          })}
                        </Svg>
                      )}
                      {propHistoryTab==='trend'&&vals.length>1&&(
                        <Svg width={chartW} height={chartH+24} style={{alignSelf:'center'}}>
                          {line>0&&(()=>{
                            const lineY = chartH-(line/maxVal*chartH);
                            return <Line x1={0} y1={lineY} x2={chartW} y2={lineY} stroke="#ff4d6d" strokeWidth={1.5} strokeDasharray="5,3"/>;
                          })()}
                          {(()=>{
                            const pts = vals.map((v,i)=>{
                              const x = (i/(vals.length-1))*(chartW-20)+10;
                              const y = chartH-(v/maxVal*chartH);
                              return `${x},${y}`;
                            });
                            const pathD = `M${pts[0]} ${pts.slice(1).map(p=>`L${p}`).join(' ')}`;
                            return(
                              <>
                                <Path d={pathD} fill="none" stroke={HRB_COLOR} strokeWidth={2}/>
                                {vals.map((v,i)=>{
                                  const x = (i/(vals.length-1))*(chartW-20)+10;
                                  const y = chartH-(v/maxVal*chartH);
                                  return(
                                    <G key={i}>
                                      <Circle cx={x} cy={y} r={4} fill={v>line?'#00e5a0':'#ff4d6d'}/>
                                      <SvgText x={x} y={y-7} fill="#e8f0f8" fontSize={9} textAnchor="middle">{v}</SvgText>
                                      <SvgText x={x} y={chartH+14} fill="#7a92a8" fontSize={8} textAnchor="middle">{rangeData[i]?.date}</SvgText>
                                    </G>
                                  );
                                })}
                              </>
                            );
                          })()}
                        </Svg>
                      )}
                    </View>
                  );
                })()}
              </>
            )}
          </View>
        </View>
      </Modal>

      {/* AGE GATE */}
      <Modal visible={ageGateVisible} transparent animationType="fade">
        <View style={{flex:1,backgroundColor:'rgba(0,0,0,0.97)',justifyContent:'center',alignItems:'center',padding:24}}>
          <Text style={{fontSize:48,marginBottom:16}}>🎰</Text>
          <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:28,textAlign:'center',marginBottom:8}}>You must be 18+</Text>
          <Text style={{color:'#7a92a8',fontSize:15,textAlign:'center',lineHeight:24,marginBottom:32}}>The Sweat Locker is intended for users 18 years of age or older. By continuing you confirm you meet this requirement.</Text>
          <TouchableOpacity style={{backgroundColor:HRB_COLOR,borderRadius:14,paddingVertical:16,paddingHorizontal:40,marginBottom:12,width:'100%',alignItems:'center'}} onPress={()=>{setAgeGateVisible(false);setOnboardingVisible(true);}}>
            <Text style={{color:'#000',fontWeight:'800',fontSize:16}}>I am 18 or older — Continue</Text>
          </TouchableOpacity>
          <TouchableOpacity style={{paddingVertical:12}} onPress={()=>setAgeGateVisible(false)}>
            <Text style={{color:'#4a6070',fontSize:13,textAlign:'center'}}>I am under 18 — Exit</Text>
          </TouchableOpacity>
        </View>
      </Modal>
      {/* ONBOARDING DISCLAIMER */}
      <Modal visible={onboardingVisible} transparent animationType="slide">
        <View style={{flex:1,backgroundColor:'rgba(0,0,0,0.97)',justifyContent:'flex-end'}}>
          <View style={{backgroundColor:'#0d1821',borderTopLeftRadius:24,borderTopRightRadius:24,padding:24,paddingBottom:40}}>
            <Text style={{fontSize:32,textAlign:'center',marginBottom:12}}>🧠</Text>
            <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:22,textAlign:'center',marginBottom:16}}>Welcome to The Sweat Locker</Text>
            <View style={{backgroundColor:'rgba(255,184,0,0.08)',borderRadius:14,padding:16,marginBottom:20,borderWidth:1,borderColor:'rgba(255,184,0,0.2)'}}>
              <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:13,marginBottom:8}}>⚠️ IMPORTANT DISCLAIMER</Text>
              <Text style={{color:'#b0c4d8',fontSize:13,lineHeight:22}}>The Sweat Locker is for entertainment and informational purposes only. This is not gambling advice. We do not place bets or guarantee results. All Sweat Scores, EV calculations, and analysis are algorithmic estimates only.{'\n\n'}Please gamble responsibly. Only bet what you can afford to lose.</Text>
            </View>
            <View style={{backgroundColor:'#0a1018',borderRadius:14,padding:16,marginBottom:20,borderWidth:1,borderColor:'#1f2d3d'}}>
              <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:12}}>🆘 Problem Gambling Resources</Text>
              <TouchableOpacity style={{flexDirection:'row',alignItems:'center',gap:10,marginBottom:12}} onPress={()=>Linking.openURL('tel:18004262537')}>
                <Text style={{fontSize:20}}>📞</Text>
                <View>
                  <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:13}}>1-800-GAMBLER</Text>
                  <Text style={{color:'#7a92a8',fontSize:11}}>National Problem Gambling Helpline</Text>
                </View>
              </TouchableOpacity>
              <TouchableOpacity style={{flexDirection:'row',alignItems:'center',gap:10,marginBottom:12}} onPress={()=>Linking.openURL('tel:18882364848')}>
                <Text style={{fontSize:20}}>📞</Text>
                <View>
                  <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:13}}>1-888-ADMIT-IT</Text>
                  <Text style={{color:'#7a92a8',fontSize:11}}>Florida Council on Compulsive Gambling</Text>
                </View>
              </TouchableOpacity>
              <TouchableOpacity style={{flexDirection:'row',alignItems:'center',gap:10}} onPress={()=>Linking.openURL('sms:18005224700&body=GAMBLER')}>
                <Text style={{fontSize:20}}>💬</Text>
                <View>
                  <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:13}}>Text GAMBLER to 1-800-522-4700</Text>
                  <Text style={{color:'#7a92a8',fontSize:11}}>24/7 Crisis Text Line</Text>
                </View>
              </TouchableOpacity>
            </View>
            <TouchableOpacity style={{backgroundColor:HRB_COLOR,borderRadius:14,paddingVertical:16,alignItems:'center',marginBottom:12}} onPress={async()=>{await AsyncStorage.setItem('hasSeenOnboarding','true');setOnboardingVisible(false);}}>
              <Text style={{color:'#000',fontWeight:'800',fontSize:16}}>I Understand — Let's Go 🔥</Text>
            </TouchableOpacity>
            <Text style={{color:'#4a6070',fontSize:11,textAlign:'center',lineHeight:16}}>By using The Sweat Locker you agree that this app is for informational and entertainment purposes only and does not constitute gambling advice.</Text>
          </View>
        </View>
      </Modal>

     <Modal visible={modalVisible} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{flex:1,justifyContent:'flex-end'}}>
          <View style={styles.modalSheet}>
  <View style={styles.modalHandle}/>
  <Text style={styles.modalTitle}>Log New Pick</Text>
  <ScrollView showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
            <Text style={styles.fieldLabel}>Matchup</Text>
            <TextInput style={styles.input} placeholder="e.g. Lakers vs Warriors" placeholderTextColor="#4a6070" value={form.matchup} onChangeText={t=>setForm({...form,matchup:t})}/>
            <Text style={styles.fieldLabel}>Your Pick</Text>
            <TextInput style={styles.input} placeholder="e.g. Lakers -2.5" placeholderTextColor="#4a6070" value={form.pick} onChangeText={t=>setForm({...form,pick:t})}/>
            <Text style={styles.fieldLabel}>Sport</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:12}}>
              <View style={{flexDirection:'row',gap:6}}>{SPORTS.map(s=>(<TouchableOpacity key={s} style={[styles.chipBtn,form.sport===s&&styles.chipBtnActive]} onPress={()=>setForm({...form,sport:s})}><Text style={[styles.chipTxt,form.sport===s&&styles.chipTxtActive]}>{s}</Text></TouchableOpacity>))}</View>
            </ScrollView>
            <Text style={styles.fieldLabel}>Bet Type</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:12}}>
              <View style={{flexDirection:'row',gap:6}}>{BET_TYPES.map(t=>(<TouchableOpacity key={t} style={[styles.chipBtn,form.type===t&&styles.chipBtnActive]} onPress={()=>setForm({...form,type:t})}><Text style={[styles.chipTxt,form.type===t&&styles.chipTxtActive]}>{t}</Text></TouchableOpacity>))}</View>
            </ScrollView>
            <View style={{flexDirection:'row',gap:8,marginBottom:12}}>
             <View style={{flex:1}}><Text style={styles.fieldLabel}>Odds</Text>
<View style={{flexDirection:'row',gap:8,marginBottom:12}}>
  <View style={{flexDirection:'row',borderRadius:10,overflow:'hidden',borderWidth:1,borderColor:'#1f2d3d'}}>
    <TouchableOpacity style={{paddingHorizontal:16,paddingVertical:12,backgroundColor:form.oddsSign==='+'?'rgba(0,229,160,0.15)':'#151c24'}} onPress={()=>setForm({...form,oddsSign:'+'})}><Text style={{color:form.oddsSign==='+'?'#00e5a0':'#7a92a8',fontWeight:'800',fontSize:18}}>+</Text></TouchableOpacity>
    <TouchableOpacity style={{paddingHorizontal:16,paddingVertical:12,backgroundColor:form.oddsSign==='-'?'rgba(255,77,109,0.15)':'#151c24'}} onPress={()=>setForm({...form,oddsSign:'-'})}><Text style={{color:form.oddsSign==='-'?'#ff4d6d':'#7a92a8',fontWeight:'800',fontSize:18}}>−</Text></TouchableOpacity>
  </View>
  <TextInput style={[styles.input,{flex:1,marginBottom:0}]} placeholder="110" placeholderTextColor="#4a6070" value={form.odds} onChangeText={t=>setForm({...form,odds:t.replace(/[^0-9]/g,'')})} keyboardType="numeric" returnKeyType="done"/>
</View>
</View>
             <View style={{flex:1}}>
  <Text style={styles.fieldLabel}>Units</Text>
  <TextInput style={styles.input} placeholder="1" placeholderTextColor="#4a6070" value={form.units} onChangeText={t=>setForm({...form,units:t})} keyboardType="numeric" returnKeyType="done"/>
  {(()=>{
   if(!form.odds || form.odds.length < 2) return null;
const oddsStr = form.oddsSign === '+' ? '+' + (form.odds||'110') : '-' + (form.odds||'110');
const dec = americanToDecimal(oddsStr);
if(dec <= 1) return null;
const impliedPct = (1/dec*100).toFixed(0);
const oddsNum = parseInt(form.odds||'110');
const isPlus = form.oddsSign === '+';

// Tiered Kelly-inspired unit suggestion
let suggestedUnits;
if(!isPlus) {
  // Favorites — bet more on big favorites
  if(oddsNum >= 300) suggestedUnits = '0.5'; // -300+ too much juice
  else if(oddsNum >= 200) suggestedUnits = '0.8';
  else if(oddsNum >= 150) suggestedUnits = '1.0';
  else if(oddsNum >= 110) suggestedUnits = '1.2'; // -110 standard
  else suggestedUnits = '1.5';
} else {
  // Underdogs — scale with value
  if(oddsNum >= 400) suggestedUnits = '0.5'; // too risky
  else if(oddsNum >= 300) suggestedUnits = '0.7';
  else if(oddsNum >= 200) suggestedUnits = '1.0';
  else if(oddsNum >= 150) suggestedUnits = '1.2';
  else if(oddsNum >= 110) suggestedUnits = '1.3';
  else suggestedUnits = '1.5'; // +100 even money
}

const isMinimum = parseFloat(suggestedUnits) <= 0.5;
    return(
      <View>
        <TouchableOpacity
          onPress={()=>setForm({...form, units:suggestedUnits})}
          style={{backgroundColor:'rgba(0,229,160,0.08)',borderRadius:8,padding:8,borderWidth:1,borderColor:'rgba(0,229,160,0.2)',marginTop:-4,marginBottom:4}}
        >
          <Text style={{color:'#00e5a0',fontSize:11,fontWeight:'700'}}>⚡ Kelly: {suggestedUnits}u — tap to apply</Text>
          <Text style={{color:'#4a6070',fontSize:10,marginTop:2}}>
            {isMinimum
              ? `Implied prob ${impliedPct}% — juice-heavy line, minimum stake advised`
              : `Implied prob ${impliedPct}% — plus-odds value, Kelly rewards the underdog`}
          </Text>
        </TouchableOpacity>
        <Text style={{color:'#4a6070',fontSize:10,marginBottom:8,paddingHorizontal:4}}>
          💡 Quarter-Kelly: sharps bet 25% of full Kelly to protect bankroll. Higher odds = more units because you're getting paid more than the risk.
        </Text>
      </View>
    );
  })()}
</View>
            </View>
            <Text style={styles.fieldLabel}>Sportsbook</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:12}}>
              <View style={{flexDirection:'row',gap:6}}>{BOOKS.map(b=>(<TouchableOpacity key={b} style={[styles.chipBtn,form.book===b&&{backgroundColor:'rgba(255,184,0,0.12)',borderColor:HRB_COLOR},form.book===b&&b!==HRB&&styles.chipBtnActive]} onPress={()=>setForm({...form,book:b})}><Text style={[styles.chipTxt,form.book===b&&{color:b===HRB?HRB_COLOR:'#00e5a0'}]}>{b===HRB?'🎸 ':''}{b}</Text></TouchableOpacity>))}</View>
            </ScrollView>
            <Text style={styles.fieldLabel}>Result</Text>
            <View style={{flexDirection:'row',gap:6,marginBottom:16}}>{RESULTS.map(r=>(<TouchableOpacity key={r} style={[styles.chipBtn,form.result===r&&styles.chipBtnActive]} onPress={()=>setForm({...form,result:r})}><Text style={[styles.chipTxt,form.result===r&&styles.chipTxtActive]}>{r}</Text></TouchableOpacity>))}</View>
            <TouchableOpacity style={styles.btnPrimary} onPress={saveBet}><Text style={styles.btnPrimaryText}>Save Pick ✓</Text></TouchableOpacity>
            <TouchableOpacity style={[styles.btnPrimary,{backgroundColor:'transparent',borderWidth:1,borderColor:'#1f2d3d',marginTop:8}]} onPress={()=>setModalVisible(false)}><Text style={[styles.btnPrimaryText,{color:'#7a92a8'}]}>Cancel</Text></TouchableOpacity>
          </ScrollView>
          </View>
          </KeyboardAvoidingView>
        </View>
      </Modal>

      {editingBet&&(
        <Modal visible={editModalVisible} animationType="slide" transparent>
          <View style={styles.modalOverlay}>
            <View style={styles.modalSheet}>
              <View style={styles.modalHandle}/>
              <Text style={styles.modalTitle}>Edit Pick</Text>
              <Text style={styles.fieldLabel}>Matchup</Text>
              <TextInput style={styles.input} placeholderTextColor="#4a6070" value={editingBet.matchup} onChangeText={t=>setEditingBet({...editingBet,matchup:t})}/>
              <Text style={styles.fieldLabel}>Pick</Text>
              <TextInput style={styles.input} placeholderTextColor="#4a6070" value={editingBet.pick} onChangeText={t=>setEditingBet({...editingBet,pick:t})}/>
              <Text style={styles.fieldLabel}>Odds</Text>
              <TextInput style={styles.input} placeholderTextColor="#4a6070" value={editingBet.odds} onChangeText={t=>setEditingBet({...editingBet,odds:t})} keyboardType="numeric" returnKeyType="done"/>
              <Text style={styles.fieldLabel}>Units</Text>
              <TextInput style={styles.input} placeholderTextColor="#4a6070" value={editingBet.units} onChangeText={t=>setEditingBet({...editingBet,units:t})} keyboardType="numeric" returnKeyType="done"/>
              <Text style={styles.fieldLabel}>Update Result</Text>
              <View style={{flexDirection:'row',gap:6,marginBottom:16,flexWrap:'wrap'}}>
                {RESULTS.map(r=>(<TouchableOpacity key={r} style={[styles.chipBtn,editingBet.result===r&&{backgroundColor:resultColor(r)+'22',borderColor:resultColor(r)}]} onPress={()=>setEditingBet({...editingBet,result:r})}><Text style={[styles.chipTxt,editingBet.result===r&&{color:resultColor(r),fontWeight:'800'}]}>{r}</Text></TouchableOpacity>))}
              </View>
              <TouchableOpacity style={styles.btnPrimary} onPress={saveEdit}><Text style={styles.btnPrimaryText}>Save Changes ✓</Text></TouchableOpacity>
              <TouchableOpacity style={[styles.btnPrimary,{backgroundColor:'transparent',borderWidth:1,borderColor:'#1f2d3d',marginTop:8}]} onPress={()=>{setEditModalVisible(false);setEditingBet(null);}}><Text style={[styles.btnPrimaryText,{color:'#7a92a8'}]}>Cancel</Text></TouchableOpacity>
            </View>
          </View>
        </Modal>
      )}

      <Modal visible={addLegModal} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{flex:1,justifyContent:'flex-end'}}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHandle}/>
            <Text style={styles.modalTitle}>Add Parlay Leg</Text>
            <Text style={styles.fieldLabel}>Matchup</Text>
            <TextInput style={styles.input} placeholder="e.g. Lakers vs Warriors" placeholderTextColor="#4a6070" value={legForm.matchup} onChangeText={t=>setLegForm({...legForm,matchup:t})}/>
            <Text style={styles.fieldLabel}>Your Pick</Text>
            <TextInput style={styles.input} placeholder="e.g. Lakers -2.5" placeholderTextColor="#4a6070" value={legForm.pick} onChangeText={t=>setLegForm({...legForm,pick:t})}/>
            <Text style={styles.fieldLabel}>Odds</Text>
            <View style={{flexDirection:'row',gap:8,marginBottom:12}}>
              <View style={{flexDirection:'row',borderRadius:10,overflow:'hidden',borderWidth:1,borderColor:'#1f2d3d'}}>
                <TouchableOpacity style={{paddingHorizontal:20,paddingVertical:12,backgroundColor:legForm.oddsSign==='+'?'rgba(0,229,160,0.15)':'#151c24'}} onPress={()=>setLegForm({...legForm,oddsSign:'+'})}><Text style={{color:legForm.oddsSign==='+'?'#00e5a0':'#7a92a8',fontWeight:'800',fontSize:18}}>+</Text></TouchableOpacity>
                <TouchableOpacity style={{paddingHorizontal:20,paddingVertical:12,backgroundColor:legForm.oddsSign==='-'?'rgba(255,77,109,0.15)':'#151c24'}} onPress={()=>setLegForm({...legForm,oddsSign:'-'})}><Text style={{color:legForm.oddsSign==='-'?'#ff4d6d':'#7a92a8',fontWeight:'800',fontSize:18}}>−</Text></TouchableOpacity>
              </View>
              <TextInput style={[styles.input,{flex:1,marginBottom:0}]} placeholder="110" placeholderTextColor="#4a6070" value={legForm.odds} onChangeText={t=>setLegForm({...legForm,odds:t.replace(/[^0-9]/g,'')})} keyboardType="numeric" returnKeyType="done"/>
            </View>
            {legForm.odds.length>0&&(
              <View style={{backgroundColor:'rgba(0,229,160,0.07)',borderRadius:10,padding:12,marginBottom:12}}>
                <Text style={{color:'#00e5a0',fontSize:13,fontWeight:'600'}}>{legForm.oddsSign}{legForm.odds} → Implied: {impliedProb(americanToDecimal(legForm.oddsSign+legForm.odds))}%</Text>
              </View>
            )}
            <TouchableOpacity style={styles.btnPrimary} onPress={addLeg}><Text style={styles.btnPrimaryText}>Add to Parlay ✓</Text></TouchableOpacity>
            <TouchableOpacity style={[styles.btnPrimary,{backgroundColor:'transparent',borderWidth:1,borderColor:'#1f2d3d',marginTop:8}]} onPress={()=>{setAddLegModal(false);setLegForm({matchup:'',pick:'',odds:'',oddsSign:'-'});}}><Text style={[styles.btnPrimaryText,{color:'#7a92a8'}]}>Cancel</Text></TouchableOpacity>
          </View>
          </KeyboardAvoidingView>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container:{flex:1,backgroundColor:'#080c10'},
  header:{paddingTop:56,paddingBottom:12,paddingHorizontal:20,flexDirection:'row',justifyContent:'space-between',alignItems:'center',borderBottomWidth:1,borderBottomColor:'#1f2d3d',backgroundColor:'rgba(8,12,16,0.95)'},
  logo:{fontSize:18,fontWeight:'800',color:'#00e5a0',letterSpacing:2},
  navIcon:{width:36,height:36,borderRadius:10,backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',alignItems:'center',justifyContent:'center'},
  parlayBadge:{backgroundColor:'rgba(255,184,0,0.15)',borderWidth:1,borderColor:'#FFB800',borderRadius:20,paddingHorizontal:10,paddingVertical:4},
  parlayBadgeText:{color:'#FFB800',fontWeight:'800',fontSize:12},
  toast:{position:'absolute',top:100,alignSelf:'center',backgroundColor:'#0e1318',borderWidth:1,borderColor:'#FFB800',borderRadius:20,paddingHorizontal:20,paddingVertical:10,zIndex:999},
  toastText:{color:'#FFB800',fontWeight:'700',fontSize:13},
  content:{flex:1,paddingHorizontal:16,paddingTop:16},
  hero:{backgroundColor:'#0e1318',borderWidth:1,borderColor:'#1f2d3d',borderRadius:20,padding:20,marginBottom:14,flexDirection:'row',justifyContent:'space-between',alignItems:'center'},
  heroSub:{fontSize:12,color:'#7a92a8',fontWeight:'600'},
  heroRecord:{fontSize:32,fontWeight:'800',color:'#e8f0f8',marginTop:2},
  heroMeta:{fontSize:12,color:'#7a92a8',marginTop:4},
  roiCircle:{width:80,height:80,borderRadius:40,borderWidth:2,borderColor:'#FFB800',alignItems:'center',justifyContent:'center'},
  roiVal:{fontSize:16,fontWeight:'800',color:'#FFB800',textAlign:'center'},
  roiLbl:{fontSize:10,color:'#7a92a8',fontWeight:'600'},
  statRow:{flexDirection:'row',gap:8,marginBottom:14},
  statBox:{flex:1,backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',borderRadius:12,padding:12,alignItems:'center'},
  statGreen:{borderColor:'rgba(0,229,160,0.3)'},
  statBlue:{borderColor:'rgba(0,153,255,0.3)'},
  statRed:{borderColor:'rgba(255,77,109,0.3)'},
  statVal:{fontSize:20,fontWeight:'800',color:'#e8f0f8'},
  statKey:{fontSize:10,color:'#7a92a8',fontWeight:'600',marginTop:2},
  sectionLabel:{fontSize:11,fontWeight:'700',letterSpacing:1.5,color:'#4a6070',marginBottom:10,marginTop:4},
  betCard:{backgroundColor:'#0e1318',borderWidth:1,borderColor:'#1f2d3d',borderLeftWidth:3,borderRadius:14,padding:14,marginBottom:10},
  gameCard:{backgroundColor:'#0e1318',borderWidth:1,borderColor:'#1f2d3d',borderRadius:16,padding:16,marginBottom:10},
  betTop:{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start'},
  betMatchup:{fontSize:14,fontWeight:'700',color:'#e8f0f8'},
  betPick:{fontSize:13,color:'#7a92a8',marginTop:3},
  pill:{paddingHorizontal:9,paddingVertical:3,borderRadius:20},
  betMeta:{flexDirection:'row',gap:6,marginTop:8,flexWrap:'wrap',alignItems:'center'},
  metaChip:{backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',borderRadius:6,paddingHorizontal:7,paddingVertical:2,fontSize:11,color:'#7a92a8'},
  quickBtn:{backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',borderRadius:8,paddingHorizontal:14,paddingVertical:6},
  oddsQuickChip:{flex:1,backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',borderRadius:10,padding:8,alignItems:'center'},
  oddsQuickLabel:{fontSize:9,fontWeight:'700',color:'#4a6070',letterSpacing:1,marginBottom:3},
  oddsQuickVal:{fontSize:12,fontWeight:'600',color:'#e8f0f8',textAlign:'center'},
  parlayLineRow:{flexDirection:'row',alignItems:'center',paddingVertical:10,borderBottomWidth:1,borderBottomColor:'#1f2d3d'},
  addParlayBtn:{backgroundColor:'rgba(0,229,160,0.12)',borderWidth:1,borderColor:'#00e5a0',borderRadius:8,paddingHorizontal:10,paddingVertical:5},
  addParlayBtnText:{color:'#00e5a0',fontSize:11,fontWeight:'700'},
  pageTitle:{fontSize:28,fontWeight:'800',color:'#e8f0f8',marginBottom:12},
  btnPrimary:{backgroundColor:'#00e5a0',borderRadius:12,padding:14,alignItems:'center',marginBottom:8},
  btnPrimaryText:{color:'#080c10',fontWeight:'700',fontSize:15},
  card:{backgroundColor:'#0e1318',borderWidth:1,borderColor:'#1f2d3d',borderRadius:16,padding:16,marginBottom:12},
  cardTitle:{fontSize:15,fontWeight:'700',color:'#e8f0f8'},
  cardSub:{fontSize:12,color:'#7a92a8',marginTop:2,marginBottom:8},
  oddsHeader:{fontSize:10,fontWeight:'700',color:'#4a6070',letterSpacing:1,flex:1,textAlign:'center'},
  oddsRow:{flexDirection:'row',alignItems:'center',paddingVertical:8,borderBottomWidth:1,borderBottomColor:'#1f2d3d'},
  oddsRowBest:{backgroundColor:'rgba(0,229,160,0.04)',borderRadius:8,paddingHorizontal:4},
  bookNameInline:{fontSize:11,fontWeight:'700',color:'#7a92a8',width:85},
  oddsVal:{fontSize:12,fontWeight:'600',color:'#e8f0f8'},
  oddsSmall:{fontSize:10,color:'#4a6070',marginTop:1},
  teamRow:{flexDirection:'row',justifyContent:'space-between',alignItems:'center',paddingVertical:10,borderBottomWidth:1,borderBottomColor:'#1f2d3d'},
  teamName:{fontSize:14,fontWeight:'600',color:'#e8f0f8'},
  teamSub:{fontSize:11,color:'#7a92a8'},
  bottomNavContainer:{backgroundColor:'rgba(8,12,16,0.97)',borderTopWidth:1,borderTopColor:'#1f2d3d',paddingBottom:24,paddingTop:8},
  bottomNav:{paddingHorizontal:8,gap:4},
  tabItem:{width:64,alignItems:'center',justifyContent:'center',gap:3},
  tabIcon:{fontSize:20},
  tabLabel:{fontSize:9,fontWeight:'700',color:'#4a6070',letterSpacing:0.5},
  tabBadge:{position:'absolute',top:-6,right:-10,backgroundColor:'#FFB800',borderRadius:8,minWidth:16,height:16,alignItems:'center',justifyContent:'center',paddingHorizontal:3},
  tabBadgeText:{color:'#080c10',fontSize:9,fontWeight:'800'},
  modalOverlay:{flex:1,backgroundColor:'rgba(0,0,0,0.7)',justifyContent:'flex-end'},
  modalSheet:{backgroundColor:'#0e1318',borderTopLeftRadius:24,borderTopRightRadius:24,padding:24,paddingBottom:40,borderWidth:1,borderColor:'#1f2d3d',maxHeight:'90%'},
  modalHandle:{width:40,height:4,backgroundColor:'#1f2d3d',borderRadius:2,alignSelf:'center',marginBottom:20},
  modalTitle:{fontSize:24,fontWeight:'800',color:'#e8f0f8',marginBottom:16},
  fieldLabel:{fontSize:11,fontWeight:'700',color:'#4a6070',letterSpacing:1,marginBottom:6},
  input:{backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d',borderRadius:10,padding:12,color:'#e8f0f8',fontSize:14,marginBottom:12},
  chipBtn:{paddingHorizontal:14,paddingVertical:6,borderRadius:20,backgroundColor:'#151c24',borderWidth:1,borderColor:'#1f2d3d'},
  chipBtnActive:{backgroundColor:'rgba(0,229,160,0.12)',borderColor:'#00e5a0'},
  chipTxt:{fontSize:12,fontWeight:'600',color:'#7a92a8'},
  chipTxtActive:{color:'#00e5a0'},
});