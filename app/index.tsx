import AsyncStorage from '@react-native-async-storage/async-storage';
import { createClient } from '@supabase/supabase-js';
import axios from 'axios';
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Linking, Modal, RefreshControl, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
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

const SPORTS = ['NBA', 'NFL', 'NHL', 'MLB', 'NCAAB', 'NCAAF'];
const BET_TYPES = ['Spread', 'Moneyline', 'Total (O/U)', 'Player Prop', 'Parlay'];
const BOOKS = ['Hard Rock', 'DraftKings', 'FanDuel', 'ESPN Bet', 'BetMGM', 'Caesars', 'Bet365'];
const RESULTS = ['Pending', 'Win', 'Loss', 'Push'];
const SPORT_KEYS = {
  NBA:'basketball_nba', NFL:'americanfootball_nfl', NHL:'icehockey_nhl',
  MLB:'baseball_mlb', NCAAB:'basketball_ncaab', NCAAF:'americanfootball_ncaaf',
};
const SPORT_EMOJI = { NBA:'🏀', NFL:'🏈', NHL:'🏒', MLB:'⚾', NCAAB:'🏀', NCAAF:'🏈' };
const BOOKMAKER_MAP = {
  'draftkings':'DraftKings','fanduel':'FanDuel','espnbet':'ESPN Bet',
  'betmgm':'BetMGM','caesars':'Caesars','bet365':'Bet365',
  'williamhill_us':'Caesars','hardrockbet':'Hard Rock','hardrock':'Hard Rock',
};
const PROP_MARKETS = {
  NBA:['player_points','player_rebounds','player_assists','player_threes'],
  NFL:['player_pass_yds','player_rush_yds','player_reception_yds','player_receptions'],
  MLB:['batter_hits','batter_home_runs','pitcher_strikeouts'],
  NHL:['player_goals','player_assists','player_shots_on_goal'],
};
const PROP_LABELS = {
  player_points:'Points', player_rebounds:'Rebounds', player_assists:'Assists',
  player_threes:'3-Pointers', player_pass_yds:'Pass Yards', player_rush_yds:'Rush Yards',
  player_reception_yds:'Rec Yards', player_receptions:'Receptions',
  batter_hits:'Hits', batter_home_runs:'Home Runs', pitcher_strikeouts:'Strikeouts',
  player_goals:'Goals', player_shots_on_goal:'Shots on Goal',
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
      'Keydets','Hokies','Orange','Big Green','Ephs','Mammoths','Lords','Yeomen',
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
export default function App() {
  const [activeTab, setActiveTab] = useState('home');
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
  const [trendsTab, setTrendsTab] = useState('ev');
  const [evData, setEvData] = useState([]);
  const [evLoading, setEvLoading] = useState(false);
  const [evSport, setEvSport] = useState('NBA');
  const [sharpData, setSharpData] = useState([]);
  const [propJerryLastUpdate, setPropJerryLastUpdate] = useState(null);
  const [sharpLoading, setSharpLoading] = useState(false);
  const [sharpSport, setSharpSport] = useState('NBA');
   const [propJerrySport, setPropJerrySport] = useState('NBA');
   const [jerryHistory, setJerryHistory] = useState([]);
  const [propJerryData, setPropJerryData] = useState([]);
  const [propJerryLoading, setPropJerryLoading] = useState(false);
  const [expandedPropJerry, setExpandedPropJerry] = useState(null);
  const [roiChartTab, setRoiChartTab] = useState('cumulative');
  const [roiTimeRange, setRoiTimeRange] = useState('all');
  const [roiUnit, setRoiUnit] = useState('units');
    const [sweatScores, setSweatScores] = useState({});
    const [historicalOdds, setHistoricalOdds] = useState({});
  const [historicalOddsLoading, setHistoricalOddsLoading] = useState({});
  const [propHistoryModal, setPropHistoryModal] = useState(false);
  const [settingsModal, setSettingsModal] = useState(false);
  const [selectedPropPlayer, setSelectedPropPlayer] = useState(null);
  const [propHistoryData, setPropHistoryData] = useState([]);
  const [propHistoryLoading, setPropHistoryLoading] = useState(false);
  const [propHistoryTab, setPropHistoryTab] = useState('bars');
  const [propHistoryRange, setPropHistoryRange] = useState('last10');
  const [propHistoryStat, setPropHistoryStat] = useState('pts');
  const [expandedSweatScore, setExpandedSweatScore] = useState(null);
  const [bartData, setBartData] = useState([]);
  const [gamesSearch, setGamesSearch] = useState('');
   const [fanmatchData, setFanmatchData] = useState({});
  const [nbaTeamData, setNbaTeamData] = useState([]);
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
  const saveUnitSize = () => { setUnitSize(tempUnitSize); saveSettings(trackingMode,tempUnitSize); setUnitSizeModal(false); };
  const usd = parseFloat(unitSize)||25;
  const formatBetSize = (units) => trackingMode==='dollars' ? '$'+(parseFloat(units||0)*usd).toFixed(0) : units+'u';

  const wins = bets.filter(b=>b.result==='Win').length;
  const losses = bets.filter(b=>b.result==='Loss').length;
  const pushes = bets.filter(b=>b.result==='Push').length;
  const totalUnits = bets.reduce((sum,b) => {
    if (b.result==='Win') return sum+parseFloat(b.units||0)*0.91;
    if (b.result==='Loss') return sum-parseFloat(b.units||0);
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
      const r = await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/scores', {
        params: {apiKey: ODDS_API_KEY, daysFrom: 3, dateFormat: 'iso'}
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
      evOpps.sort((a,b)=>{
        if(a.isHRB&&!b.isHRB) return -1;
        if(!a.isHRB&&b.isHRB) return 1;
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
        params:{apiKey:ODDS_API_KEY,regions:'us,us2',markets:'spreads,h2h',oddsFormat:'american',bookmakers:'hardrock,draftkings,fanduel,betmgm,caesars'}
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
        params:{apiKey:ODDS_API_KEY,regions:'us,us2',markets:'spreads,totals,h2h',oddsFormat:'american',bookmakers:'hardrock,draftkings,fanduel,espnbet,betmgm,caesars,bet365'}
      });
      setOddsData(r.data);
    }catch(e){setOddsData([]);}
    setOddsLoading(false);setRefreshing(false);
  };

  const fetchGames = async (sport=gamesSport,day=gamesDay) => {
    setGamesLoading(true);
    try {
      const r=await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/odds',{
        params:{apiKey:ODDS_API_KEY,regions:'us,us2',markets:'spreads,totals,h2h',oddsFormat:'american',bookmakers:'hardrockbet,draftkings,fanduel,espnbet,betmgm,caesars,williamhill_us,bet365'}
      });
      const now=new Date();
      const todayStart=new Date(now);todayStart.setHours(0,0,0,0);
      const todayEnd=new Date(now);todayEnd.setHours(23,59,59,999);
      const tomorrowStart=new Date(todayEnd);tomorrowStart.setDate(tomorrowStart.getDate()+1);tomorrowStart.setHours(0,0,0,0);
      const tomorrowEnd=new Date(tomorrowStart);tomorrowEnd.setHours(23,59,59,999);
      const yesterdayStart=new Date(todayStart);yesterdayStart.setDate(yesterdayStart.getDate()-1);
      const filtered=r.data.filter(game=>{
        const t=new Date(game.commence_time);
        if(day==='today')return t>=todayStart&&t<=todayEnd;
        if(day==='tomorrow')return t>=tomorrowStart&&t<=tomorrowEnd;
        if(day==='yesterday')return t>=yesterdayStart&&t<todayStart;
        return true;
      });
      const mappedGames = filtered.map(g => ({
        ...g,
        away_team: sport==='NCAAB' ? stripMascot(g.away_team) : g.away_team,
        home_team: sport==='NCAAB' ? stripMascot(g.home_team) : g.home_team,
      }));
      setGamesData(mappedGames);
      try {
        await AsyncStorage.setItem(GAMES_CACHE_KEY+'_'+sport+'_'+day, JSON.stringify({data:mappedGames, timestamp:Date.now()}));
      } catch(e) {}
    }catch(e){setGamesData([]);}
    setGamesLoading(false);setRefreshing(false);

  };

  const fetchProps = async (sport=propsSport) => {
    if(!PROP_MARKETS[sport]){setPropsData([]);return;}
    setPropsLoading(true);
    try {
      const gamesResp=await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/odds',{
        params:{apiKey:ODDS_API_KEY,regions:'us,us2',markets:'h2h',oddsFormat:'american'}
      });
      const allProps=[];
      for(const game of gamesResp.data.slice(0,3)){
        try{
          const pr=await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/events/'+game.id+'/odds',{
            params:{apiKey:ODDS_API_KEY,regions:'us,us2',markets:PROP_MARKETS[sport].join(','),oddsFormat:'american',bookmakers:'hardrock,draftkings,fanduel,betmgm'}
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
      setPropsData(allProps.slice(0,30));
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

  // Build a lookup map for four factors by team name
  const ffMap = {};
  ffData.forEach(t => { ffMap[t.TeamName] = t; });

  const mapped = ratingsData.map(t => {
    const ff = ffMap[t.TeamName] || {};
    return {
      team: t.TeamName,
      // Efficiency
      adjOE: parseFloat(t.AdjOE) || 109.4,
      adjDE: parseFloat(t.AdjDE) || 109.4,
      adjEM: (parseFloat(t.AdjOE) || 0) - (parseFloat(t.AdjDE) || 0),
      adjOERank: parseInt(t.RankAdjOE) || 0,
      adjDERank: parseInt(t.RankAdjDE) || 0,
      // Tempo
      tempo: parseFloat(t.AdjTempo) || 68.0,
      tempoRank: parseInt(t.RankAdjTempo) || 0,
      // Four Factors — Offense
      eFG_O: parseFloat(ff.eFG_Pct) || 0,
      eFG_O_rank: parseInt(ff.RankeFG_Pct) || 0,
      to_O: parseFloat(ff.TO_Pct) || 0,
      to_O_rank: parseInt(ff.RankTO_Pct) || 0,
      or_O: parseFloat(ff.OR_Pct) || 0,
      or_O_rank: parseInt(ff.RankOR_Pct) || 0,
      ftr_O: parseFloat(ff.FT_Rate) || 0,
      ftr_O_rank: parseInt(ff.RankFT_Rate) || 0,
      // Four Factors — Defense
      eFG_D: parseFloat(ff.DeFG_Pct) || 0,
      eFG_D_rank: parseInt(ff.RankDeFG_Pct) || 0,
      to_D: parseFloat(ff.DTO_Pct) || 0,
      to_D_rank: parseInt(ff.RankDTO_Pct) || 0,
      or_D: parseFloat(ff.DOR_Pct) || 0,
      or_D_rank: parseInt(ff.RankDOR_Pct) || 0,
      ftr_D: parseFloat(ff.DFT_Rate) || 0,
      ftr_D_rank: parseInt(ff.RankDFT_Rate) || 0,
      // Record / meta
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
  await AsyncStorage.setItem(BART_CACHE_KEY, JSON.stringify({data: mapped, timestamp: Date.now()}));
} catch(e) { console.log('BartData fetch error:', e.message); }
  };

 const FANMATCH_CACHE_KEY = 'sweatlocker_fanmatch_cache';
 const PROP_JERRY_CACHE_KEY = 'sweatlocker_jerry_cache';

const fetchKenpomFanmatch = async () => {
  try {
    const now = new Date();
    const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    const today = fmt(now);
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
      r = await axios.get('https://kenpom.com/api.php', {
        params: {endpoint:'fanmatch', d:yesterday},
        headers: {Authorization:`Bearer ${KENPOM_KEY}`},
        timeout: 15000,
      });
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
      console.log('NBA team data error:', e?.message);
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
            if(idx !== -1) { newBets[idx] = {...newBets[idx], result}; updated = true; fetchPickRecap(bet, result); }
          }
        }
      }
      if(updated) setBets(newBets);
    } catch(e) {
      //console.log('Auto detect error:', e?.message);
    }
  }; 
  const fetchScores = async (sport) => {
    if(scoresCache[sport]) return scoresCache[sport];
    try {
      const r = await axios.get('https://api.the-odds-api.com/v4/sports/'+SPORT_KEYS[sport]+'/scores', {
        params: {apiKey: ODDS_API_KEY, daysFrom: 3, dateFormat: 'iso'}
      });
      //console.log('Scores raw count:', r.data?.length);
      const completed = (r.data||[]).filter(g => g.completed);
      //console.log('Scores fetched:', sport, 'total:', r.data?.length, 'completed:', completed.length);
      setScoresCache(prev => ({...prev, [sport]: completed}));
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
    if(score >= 74) return {label:'🔥 Prime Sweat', color:'#ff4d6d'};
    if(score >= 60) return {label:'✅ Solid Lock', color:'#00e5a0'};
    if(score >= 40) return {label:'👀 Worth a Look', color:'#ffd166'};
    return {label:'❌ Pass', color:'#4a6070'};
  };

    const calcGameSweatScore = (game, sport, fanmatchData = {}) => {
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
    if(sport==='NCAAB') {
//console.log('BARTDATA FLORIDA:', bartData.filter(t=>t.team.toLowerCase().includes('florida')).map(t=>t.team));  
      const awayStripped = normalizeTeamName(stripMascot(game.away_team)).toLowerCase().trim();
  const homeStripped = normalizeTeamName(stripMascot(game.home_team)).toLowerCase().trim();
 //console.log('NCAAB MATCH ATTEMPT:', game.away_team, '->', awayStripped, '|', game.home_team, '->', homeStripped);
  //console.log('FANMATCH KEYS:', Object.keys(fanmatchData||{}).slice(0,5));

  let fanmatchGame = null;
  Object.values(fanmatchData||{}).forEach(fg => {
    const fVisitor = (fg.visitor||'').toLowerCase().trim();
    const fHome = (fg.home||'').toLowerCase().trim();
    const visitorMatch = fVisitor === awayStripped || fVisitor.startsWith(awayStripped + ' ') || awayStripped.startsWith(fVisitor + ' ');
const homeMatch = fHome === homeStripped || fHome.startsWith(homeStripped + ' ') || homeStripped.startsWith(fHome + ' ');
    if(visitorMatch && homeMatch) fanmatchGame = fg;
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
  projectedTotal = ((((awayTeam.adjOE + homeTeam.adjOE) / 2) + 
                     ((awayTeam.adjDE + homeTeam.adjDE) / 2)) / 2 / 100 * projPossessions * 2).toFixed(1);

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
    if(histData?.openingSpread !== null && histData?.openingSpread !== undefined && allSpreads.length > 0) {
      const currentSpread = allSpreads[0];
      const realMovement = Math.abs(histData.openingSpread - currentSpread);
      lineTrajectory = realMovement >= 3 ? 95 : realMovement >= 2 ? 85 : realMovement >= 1 ? 70 : realMovement >= 0.5 ? 55 : 35;
    }

    // 4. SHARP SIGNAL (20%)
    const mlOdds = bookmakers.map(bm => {
      const ml = bm.markets && bm.markets.find(m => m.key==='h2h');
      return ml && ml.outcomes && ml.outcomes[0] ? ml.outcomes[0].price : null;
    }).filter(x => x !== null);
    const mlVariance = mlOdds.length > 1 ? Math.max(...mlOdds) - Math.min(...mlOdds) : 0;
    const sharpSignal = mlVariance > 20 ? 80 : mlVariance > 10 ? 60 : mlVariance > 5 ? 45 : 30;

    // 5. SITUATIONAL EDGE (15%)
    let situationalEdge = 50;
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

    let total = Math.round(
      (marketEfficiency * 0.15) +
      (modelMismatch * 0.30) +
      (lineTrajectory * 0.20) +
      (sharpSignal * 0.15) +
      (situationalEdge * 0.20)
    );
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
  // NEW
  fourFactorBoost,
  efgMismatch,
  projectedTotal,
  mismatchPts,
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

    // Directional lean
    let leanSide = null;
    let leanBet = null;
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
  let favTeam;
  if(sport === 'NCAAB' && spreadEdge !== 0) {
    // Use model edge — positive spreadEdge means home team undervalued
    favTeam = spreadEdge > 0 ? stripMascot(game.home_team) : stripMascot(game.away_team);
  } else {
    favTeam = avgAwayML < avgHomeML ? stripMascot(game.away_team) : stripMascot(game.home_team);
  }
  const favSpread = allSpreads.length ? Math.min(...allSpreads.map(Math.abs)).toFixed(1) : null;
  leanSide = favTeam+(favSpread ? ' -'+favSpread : '');
  leanBet = 'spread';
}
    }
    return {
      total,
      leanSide, leanBet,
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
        pick: (projectedTotal && postedTotal && projectedTotal > postedTotal ? 'Over ' : 'Under ')+totalLine.point,
        odds: totalLine.price,
        book: totalBook||HRB,
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
    return calcGameSweatScore(game, sport, fanmatchData);
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
      const legs = parlayLegs.map((l,i) => `Leg ${i+1}: ${l.pick} (${l.matchup}) at ${l.oddsSign}${l.odds}`).join('\n');
      const prompt = `You are Jerry, sharp AI analyst for The Sweat Locker sports betting app.

Parlay legs:
${legs}
Combined odds: ${parlayAmerican}
Implied probability: ${parlayProb}%
Legs: ${parlayLegs.length}

Search the web for current injury reports, recent form, and line movement for each team or player in these parlay legs. Then analyze this parlay in exactly 3 sentences. Sentence 1 — call out the strongest leg and why based on what you found. Sentence 2 — identify the biggest risk leg with a specific reason. Sentence 3 — give a sharp overall verdict on whether the juice is worth the squeeze. Never say "bet" or "must play". Be direct and confident like a seasoned handicapper. Never mention KenPom.`;

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
          max_tokens:1000,
          tools:[{type:'web_search_20250305',name:'web_search'}],
          messages:[{role:'user',content:prompt}]
        })
      });
      const data = await response.json();
      const text = data?.content?.filter(b=>b.type==='text').map(b=>b.text).join('') || '';
      setParlayAnalysis(text);
    } catch(e) {
      setParlayAnalysis("Jerry couldn't break this one down right now. Check your legs manually and trust the math.");
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

  const fetchDailyBriefing = async () => {
    try {
      const cached = await AsyncStorage.getItem('sweatlocker_briefing_cache');
      if(cached) {
        const parsed = JSON.parse(cached);
        const ageMin = (Date.now() - parsed.timestamp) / 60000;
        if(ageMin < 360) { setDailyBriefing(parsed.text); return; }
      }
    } catch(e) {}
    setDailyBriefingLoading(true);
    try {
      const today = new Date().toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric'});
      const wins = bets.filter(b=>b.result==='Win').length;
      const losses = bets.filter(b=>b.result==='Loss').length;
      const pending = bets.filter(b=>b.result==='Pending').length;
     const now = new Date();
      const todayGames = gamesData
        .filter(g => g && g.away_team && g.home_team && new Date(g.commence_time) > now)
        .slice(0,10)
        .map(g=>`${g.away_team} vs ${g.home_team}`)
        .join('\n');

      const prompt = `You are Jerry, sharp AI analyst for The Sweat Locker. Confident, energetic, like a seasoned handicapper. Today is ${today}. User record: ${wins}-${losses}. Pending: ${pending}. Today's games: ${todayGames || 'NBA and NCAAB slate today'}. March Madness is priority if tournament games are on the slate. Write exactly 3 sharp sentences. Lead with a real injury, line move, or sharp angle on any game being played today. Give the betting angle. Fire them up. Never say what you could not find or what is not available. Never mention the absence of games or data. Just deliver the sharpest take available. End with — Jerry.`;

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
          tools:[{type:'web_search_20250305',name:'web_search'}],
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
      const data = {
        name: `${player.first_name} ${player.last_name}`,
        team: player.team?.abbreviation || '',
        last5: {
          pts: avg('pts'),
          reb: avg('reb'),
          ast: avg('ast'),
          min: avg('min')
        }
      };
      await AsyncStorage.setItem(cacheKey, JSON.stringify({data, timestamp: Date.now()}));
      return data;
    } catch(e) {
      return null;
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

const modelContext = scoreData?.predictedSpread ? `
SWEAT LOCKER MODEL DATA:
- Projected spread: ${predictedSpread > 0 ? game.home_team.split(' ').pop() : game.away_team.split(' ').pop()} by ${Math.abs(predictedSpread).toFixed(1)}
- Edge vs posted line: ${spreadEdge > 0 ? '+' : ''}${spreadEdge.toFixed(1)} pts ${Math.abs(spreadEdge) >= 3 ? '⚠️ SIGNIFICANT' : Math.abs(spreadEdge) >= 1.5 ? '(notable)' : '(small)'}
- Win probability: ${homeWP ? `Home ${(homeWP*100).toFixed(0)}% / Away ${((1-homeWP)*100).toFixed(0)}%` : 'N/A'}
- SOS gap: ${Math.abs(sosDelta).toFixed(2)} ${Math.abs(sosDelta) > 3 ? '(LARGE — significant schedule strength difference)' : Math.abs(sosDelta) > 1.5 ? '(moderate)' : '(small)'}
- Luck factor: ${luckAdjustment > 0.5 ? 'Away team significantly unlucky — true talent better than record' : luckAdjustment < -0.5 ? 'Home team significantly unlucky — true talent better than record' : luckAdjustment > 0 ? 'Slight away team luck edge' : luckAdjustment < 0 ? 'Slight home team luck edge' : 'Neutral'}
${awayTeamData ? `- ${game.away_team.split(' ').pop()} efficiency: AdjOE ${awayTeamData.adjOE?.toFixed(1)} / AdjDE ${awayTeamData.adjDE?.toFixed(1)} / AdjEM ${awayTeamData.adjEM?.toFixed(1)}` : ''}
${homeTeamData ? `- ${game.home_team.split(' ').pop()} efficiency: AdjOE ${homeTeamData.adjOE?.toFixed(1)} / AdjDE ${homeTeamData.adjDE?.toFixed(1)} / AdjEM ${homeTeamData.adjEM?.toFixed(1)}` : ''}
- Data source: ${hasFanmatch ? 'Game-specific model prediction' : 'Season efficiency model'}
` : '';

    setGameNarrative('');
    setGameNarrativeLoading(true);
    try {
      const sport = gamesSport || 'NBA';
      const spread = game?.bookmakers?.[0]?.markets?.find(m=>m.key==='spreads')?.outcomes?.[0];
      const total = game?.bookmakers?.[0]?.markets?.find(m=>m.key==='totals')?.outcomes?.[0];
      const prompt = `You are Jerry, a sharp sports analyst for The Sweat Locker app. Confident, direct, no fluff.

Game: ${game.away_team} @ ${game.home_team}
Sport: ${sport}
Sweat Score: ${score}/100${score>=80?' (PRIME SWEAT 🔒)':score>=65?' (Strong lean)':' (Monitor)'}
Spread: ${spread?`${spread.name} ${spread.point > 0 ? '+' : ''}${spread.point} (${spread.point < 0 ? 'FAVORITE' : 'UNDERDOG'})`:'N/A'}
${modelContext}
  Rules you must follow:
- Reference the EXACT Sweat Score number given above — do not invent or change it
- Focus ONLY on the spread and Sweat Score — do not lean or analyze the total
- Base analysis ONLY on data provided — no injuries, news, or outside knowledge
- If Sweat Score is 75+ tell users this is Prime Sweat. If 65-74 it's a strong lean. Below 65 be cautious in tone. Never contradict the score tier.
- When modelContext data is present, reference the most significant factor (spread edge, SOS, luck, or eFG%) in your take
- Do NOT mention home court advantage — tournament games are at neutral sites
-Never mention KenPom by name - refer to it as the "Sweat Locker efficiency model" or "Sweat Locker game model"
- When referencing the model, say which team it favors to cover, not raw numbers
- 2 sentences maximum. Be sharp and direct.`;

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
        body:JSON.stringify({model:'claude-haiku-4-5-20251001',max_tokens:1000,messages:[{role:'user',content:prompt}]})
      });
      clearTimeout(timeout);
      const data = await response.json();
      //console.log('Jerry response status:', response.status);
      //console.log('Jerry response data:', JSON.stringify(data));
      const text = data?.content?.[0]?.text || '';
      setGameNarrative(text);
    } catch(e) {
      //console.log('Jerry error:', e.message);
      setGameNarrative('Jerry is reviewing the tape on this one. Check back shortly.');
    }
    setGameNarrativeLoading(false);
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

  const fetchPropJerry = async (sport=propJerrySport) => {
    
setPropJerryLoading(true);
    setPropJerryData([]);
    try {
      const sportKey = SPORT_KEYS[sport];
      if(!sportKey) { setPropJerryLoading(false); return; }

      // Load cache first
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
     
      const markets = sport==='NBA' ?
        'player_points,player_rebounds,player_assists,player_threes' :
        sport==='NFL' ? 'player_pass_yards,player_rush_yards,player_reception_yards,player_anytime_td' :
        sport==='NHL' ? 'player_points,player_goals,player_assists' :
        'player_points,player_rebounds,player_assists';

      const resp = await axios.get(`https://api.the-odds-api.com/v4/sports/${sportKey}/events`, {
        params: {apiKey: ODDS_API_KEY, dateFormat: 'iso'}
      });
      const events = resp.data || [];
      if(!events.length) { setPropJerryLoading(false); return; }

      const propMap = {};
     
      await Promise.all(events.slice(0,8).map(async event => {
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
                const side = outcome.name?.toLowerCase().includes('over') ? 'over' : 'under';
                if(side==='over') propMap[key].overLines.push({book: BOOKMAKER_MAP[bm.key]||bm.key, line, odds});
                else propMap[key].underLines.push({book: BOOKMAKER_MAP[bm.key]||bm.key, line, odds});
                propMap[key].lines.push({book: BOOKMAKER_MAP[bm.key]||bm.key, line, odds, side});
              });
            });
          });
        } catch(e) {}
      }));

      // Grade each prop
      const propEntries = Object.values(propMap).slice(0,15);
const gradedRaw = [];
for(let pi = 0; pi < propEntries.length; pi++) {
  const prop = propEntries[pi];
  if(pi > 0) await new Promise(r => setTimeout(r, 150));
  gradedRaw.push(await (async (prop) => {
        const overOdds = prop.overLines.map(l=>l.odds);
        const underOdds = prop.underLines.map(l=>l.odds);
        if(!overOdds.length || !underOdds.length) return null;

        // Best lines
        const bestOver = prop.overLines.reduce((best,l) => l.odds > (best?.odds||-9999) ? l : best, null);
        const bestUnder = prop.underLines.reduce((best,l) => l.odds > (best?.odds||-9999) ? l : best, null);
       
        // EV calculation
        const avgOverOdds = overOdds.reduce((a,b)=>a+b,0)/overOdds.length;
        const avgUnderOdds = underOdds.reduce((a,b)=>a+b,0)/underOdds.length;
        const overProb = avgOverOdds < 0 ? Math.abs(avgOverOdds)/(Math.abs(avgOverOdds)+100) : 100/(avgOverOdds+100);
        const underProb = avgUnderOdds < 0 ? Math.abs(avgUnderOdds)/(Math.abs(avgUnderOdds)+100) : 100/(avgUnderOdds+100);
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

           if(bestEV >= 4 && bookCount >= 4 && lineRange <= 0.5) {
          grade='A'; gradeColor='#00e5a0';
        } else if(bestEV >= 3 && bookCount >= 3 && lineRange <= 1.0) {
          grade='B'; gradeColor='#FFB800';
        } else if(bestEV >= 1) {
          grade='C'; gradeColor='#0099ff';
        } else {
          grade='D'; gradeColor='#ff4d6d';
        }
        // AI Jerry narration
        try {
          const gradeContext = grade==='A' ? 'This is a strong edge — be enthusiastic but not reckless' :
                              grade==='B' ? 'This is a solid edge — confident but measured' :
                              grade==='C' ? 'This is a mild edge — cautious and analytical' :
                              'No real edge here — advise passing';
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
                      playerContext = ` Player last 5 games avg: ${stats.last5.pts}pts, ${stats.last5.reb}reb, ${stats.last5.ast}ast, ${stats.last5.min}min.`;
                    }
                  }
                  return `You are Prop Jerry, a sharp and entertaining sports betting analyst with a big personality. Generate a single 2-sentence insight for this prop: ${prop.player} ${bestSide} ${bestLine?.line}, ${bestEV.toFixed(1)}% EV across ${bookCount} books, line consensus range: ${lineRange.toFixed(1)} pts. Grade: ${grade}. ${gradeContext}.${kenpomContext}${playerContext} Base analysis ONLY on data provided. Never say "bet" or "must play". Keep it under 120 characters total. Sound like a real analyst not a robot.`;
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
          bestOverEV, bestUnderEV,
        };
       })(prop));
}
const graded = gradedRaw.filter(p => {
  if(!p || p.bestEV <= 0) return false;
  const odds = parseFloat(p.bestLine?.odds);
  if(isNaN(odds)) return true;
  const minBooks = propJerrySport==='NHL' ? 1 : 2;
  return Math.abs(odds) <= 350 && p.bookCount >= minBooks;
})

        .sort((a,b) => b.bestEV - a.bestEV)
        .slice(0,30);

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
    } catch(e) {
      //console.log('PropJerry error:', e?.message);
    }
    setPropJerryLastUpdate(new Date());
setPropJerryLoading(false);
    setPropJerryLoading(false);
  };

  useEffect(()=>{if(activeTab==='odds')fetchOdds(oddsSport);},[activeTab,oddsSport]);
  useEffect(()=>{if(activeTab==='games')fetchGames(gamesSport,gamesDay);},[activeTab,gamesSport,gamesDay,bartData.length]);
  useEffect(()=>{
    if(activeTab==='stats'){
      if(statsTab==='props')fetchProps(propsSport);
      if(statsTab==='players')fetchPlayerStats();
    }
  },[activeTab,statsTab,propsSport]);
 useEffect(()=>{
    if(activeTab==='trends'){
      if(trendsTab==='ev')fetchEV(evSport);
      if(trendsTab==='sharp')fetchSharp(sharpSport);
      if(trendsTab==='propjerry')fetchPropJerry(propJerrySport);
    }
  },[activeTab,trendsTab,evSport,sharpSport,propJerrySport]);
    useEffect(()=>{
    if(!gameDetailModal||!selectedGame) return;
    setScheduleGamesLoading(true);
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
    else if(activeTab==='games')fetchGames(gamesSport,gamesDay);
    else if(activeTab==='stats'){if(statsTab==='props')fetchProps(propsSport);else fetchPlayerStats();}
    else if(activeTab==='trends'){if(trendsTab==='ev')fetchEV(evSport);else if(trendsTab==='sharp')fetchSharp(sharpSport);else setRefreshing(false);}
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
              <Text style={{color:'#c8d8e8',fontSize:13,lineHeight:20,fontStyle:'italic'}}>"{gameNarrative}"</Text>
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
                {isReal&&<View style={{backgroundColor:'rgba(0,229,160,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,borderWidth:1,borderColor:'rgba(0,229,160,0.3)',overflow:'hidden'}}><Text style={{color:'#00e5a0',fontSize:9,fontWeight:'800'}}>📡 LIVE - last 3 days</Text></View>}
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
          const isNCAAB = gamesSport==='NCAAB';
          const isNBA = gamesSport==='NBA';
          const awayReal = isNCAAB ? fuzzyMatchTeam(md.away, bartData, 'team') : isNBA ? fuzzyMatchTeam(md.away, nbaTeamData, 'team') : null;
          const homeReal = isNCAAB ? fuzzyMatchTeam(md.home, bartData, 'team') : isNBA ? fuzzyMatchTeam(md.home, nbaTeamData, 'team') : null;
          const hasReal = awayReal && homeReal;
          const statCats = hasReal ? [
            {label:'Off Efficiency', away: awayReal.adjOERank, home: homeReal.adjOERank},
            {label:'Def Efficiency', away: awayReal.adjDERank, home: homeReal.adjDERank},
            {label:'Tempo', away: awayReal.tempoRank, home: homeReal.tempoRank},
          ] : md.statCategories;
          const totalTeams = isNCAAB ? bartData.length||358 : 30;
          return(
            <View style={{backgroundColor:'#0a1018',borderRadius:14,padding:14,borderWidth:1,borderColor:'#1f2d3d'}}>
              <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
                <View style={{flexDirection:'row',gap:8}}>
                  {[{id:'offense',label:'Offense'},{id:'defense',label:'Defense'}].map(t=>(
                    <TouchableOpacity key={t.id} style={[{paddingHorizontal:12,paddingVertical:6,borderRadius:10,borderWidth:1,borderColor:'#1f2d3d',backgroundColor:'#151c24'},statView===t.id&&{backgroundColor:'rgba(255,184,0,0.12)',borderColor:HRB_COLOR}]} onPress={()=>setStatView(t.id)}>
                      <Text style={{color:statView===t.id?HRB_COLOR:'#7a92a8',fontWeight:'700',fontSize:11}}>{t.label}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
                {hasReal&&<View style={{backgroundColor:'rgba(0,229,160,0.1)',borderRadius:6,paddingHorizontal:8,paddingVertical:3,borderWidth:1,borderColor:'rgba(0,229,160,0.3)'}}><Text style={{color:'#00e5a0',fontSize:9,fontWeight:'800'}}>📡 LIVE DATA</Text></View>}
              </View>
              <View style={{flexDirection:'row',justifyContent:'space-between',marginBottom:10}}>
                <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:12}}>{awayShort}</Text>
                <Text style={{color:'#4a6070',fontSize:11,fontWeight:'600'}}>STAT CATEGORY</Text>
                <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:12}}>{homeShort}</Text>
              </View>
              {(hasReal ? statCats : md.statCategories).map((stat,i)=>{
               const awayRank = stat.away || 0;
               const homeRank = stat.home || 0;

                const awayColor = rankColor(awayRank, totalTeams);
                const homeColor = rankColor(homeRank, totalTeams);
                return(
                  <View key={i} style={{flexDirection:'row',alignItems:'center',marginBottom:10}}>
                    <View style={{flex:1,alignItems:'flex-start'}}>
                      <View style={{paddingHorizontal:10,paddingVertical:5,borderRadius:8,backgroundColor:awayColor+'22',borderWidth:1,borderColor:awayColor+'44',minWidth:48,alignItems:'center'}}>
                        <Text style={{color:awayColor,fontWeight:'800',fontSize:12}}>{isNCAAB?'#':''}{awayRank}</Text>
                      </View>
                    </View>
                    <Text style={{flex:1.5,color:'#7a92a8',fontSize:11,textAlign:'center'}}>{stat.label}</Text>
                    <View style={{flex:1,alignItems:'flex-end'}}>
                      <View style={{paddingHorizontal:10,paddingVertical:5,borderRadius:8,backgroundColor:homeColor+'22',borderWidth:1,borderColor:homeColor+'44',minWidth:48,alignItems:'center'}}>
                        <Text style={{color:homeColor,fontWeight:'800',fontSize:12}}>{isNCAAB?'#':''}{homeRank}</Text>
                      </View>
                    </View>
                  </View>
                );
              })}
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
              {!hasReal&&<Text style={{color:'#4a6070',fontSize:10,textAlign:'right',marginTop:4}}>* Simulated — select NCAAB or NBA for live data</Text>}
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
  const openGameDetail=(game)=>{setSelectedGame(game);setMatchupTab('money');setScheduleTeam('away');setSitMarket('spread');setStatView('offense');setGameDetailModal(true);fetchHistoricalOdds(game, gamesSport);fetchGameNarrative(game, calcGameSweatScore(game, gamesSport, fanmatchData));};
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
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>AI-powered sports analytics built for bettors who want a real edge — not just vibes.</Text>
            </View>
          )}
          {onboardingStep===1&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>🔥</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:30,textAlign:'center',marginBottom:12}}>Sweat Score</Text>
              <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:16,textAlign:'center',marginBottom:20}}>Every game graded 0-100</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>We analyze market efficiency, sharp money movement, line variance, and our analytics model to find mispriced lines.{'\n\n'}{'🔒 75+ = Prime Sweat\nOur highest confidence signal.'}</Text>
            </View>
          )}
          {onboardingStep===2&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>🎤</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:30,textAlign:'center',marginBottom:12}}>Meet Prop Jerry</Text>
              <Text style={{color:'#00e5a0',fontWeight:'700',fontSize:16,textAlign:'center',marginBottom:20}}>Your AI prop analyst</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>Jerry is your AI analyst — he grades props A through D based on real EV math, narrates every game with sharp insight, briefs you every morning, and reacts to your results in real time.{'\n\n'}A grades hitting 77% this season.{'\n'}Jerry never sleeps. You make the call.</Text>
            </View>
          )}
          {onboardingStep===3&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>📈</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:30,textAlign:'center',marginBottom:12}}>Track Your Edge</Text>
              <Text style={{color:'#ffd166',fontWeight:'700',fontSize:16,textAlign:'center',marginBottom:20}}>No hiding from the data</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>Log every pick and track your real performance. Win rate, units, profit/loss — all in one place.{'\n\n'}The only way to know if you have an edge is to track it honestly.</Text>
            </View>
          )}
          {onboardingStep===4&&(
            <View style={{flex:1,justifyContent:'center',alignItems:'center'}}>
              <Text style={{fontSize:64,marginBottom:24}}>⚠️</Text>
              <Text style={{color:'#e8f0f8',fontWeight:'900',fontSize:30,textAlign:'center',marginBottom:12}}>One More Thing</Text>
              <Text style={{color:'#7a92a8',fontSize:14,textAlign:'center',lineHeight:22}}>The Sweat Locker provides data analysis for entertainment purposes only.{'\n\n'}Past performance is not indicative of future results.{'\n\n'}Must be 21+ to use this app. Know your local laws and gamble responsibly.</Text>
            </View>
          )}
          <View style={{flexDirection:'row',justifyContent:'center',gap:8,marginBottom:28}}>
            {[0,1,2,3,4].map(i=>(
              <View key={i} style={{width:i===onboardingStep?24:8,height:8,borderRadius:4,backgroundColor:i===onboardingStep?HRB_COLOR:'#1f2d3d'}}/>
            ))}
          </View>
          <View style={{gap:12}}>
            {onboardingStep<4?(
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
            {onboardingStep<4&&(
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
        <View style={{position:'absolute',bottom:90,left:16,right:16,backgroundColor:'#0d1f2d',borderRadius:16,padding:16,borderWidth:1,borderColor:HRB_COLOR,zIndex:998,shadowColor:'#000',shadowOffset:{width:0,height:4},shadowOpacity:0.4,shadowRadius:8}}>
          <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
            <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:12}}>🎤 JERRY'S PARLAY ANALYSIS</Text>
            <TouchableOpacity onPress={()=>setParlayAnalysisVisible(false)}>
              <Text style={{color:'#4a6070',fontSize:13}}>✕ Close</Text>
            </TouchableOpacity>
          </View>
          {parlayAnalysisLoading?(
            <View style={{flexDirection:'row',alignItems:'center',gap:8}}>
              <ActivityIndicator size='small' color={HRB_COLOR}/>
              <Text style={{color:'#4a6070',fontSize:13}}>Jerry is breaking down your parlay...</Text>
            </View>
          ):(
            <Text style={{color:'#c8d8e8',fontSize:13,lineHeight:20,fontStyle:'italic'}}>"{parlayAnalysis}"</Text>
          )}
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
            <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
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
                <Text style={styles.roiLbl}>{trackingMode==='units'?'UNITS':'P/L'}</Text>
              </View>
            </View>
            <View style={styles.statRow}>
              <View style={[styles.statBox,styles.statGreen]}><Text style={[styles.statVal,{color:'#00e5a0'}]}>{winRate}%</Text><Text style={styles.statKey}>Win Rate</Text></View>
              <View style={[styles.statBox,styles.statBlue]}><Text style={[styles.statVal,{color:'#0099ff',fontSize:trackingMode==='dollars'?16:20}]}>{trackingMode==='units'?(totalUnits>=0?'+':'')+totalUnits.toFixed(1)+'u':(totalDollars>=0?'+':'-')+'$'+Math.abs(totalDollars).toFixed(0)}</Text><Text style={styles.statKey}>{trackingMode==='units'?'Units':'P/L'}</Text></View>
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

        {activeTab==='picks'&&(
          <View>
            <Text style={styles.pageTitle}>My Picks</Text>
            <View style={styles.statRow}>
              <View style={[styles.statBox,styles.statGreen]}><Text style={[styles.statVal,{color:'#00e5a0'}]}>{wins}W</Text><Text style={styles.statKey}>Wins</Text></View>
              <View style={[styles.statBox,styles.statRed]}><Text style={[styles.statVal,{color:'#ff4d6d'}]}>{losses}L</Text><Text style={styles.statKey}>Losses</Text></View>
              <View style={[styles.statBox,styles.statBlue]}><Text style={[styles.statVal,{color:'#0099ff',fontSize:trackingMode==='dollars'?15:20}]}>{trackingMode==='units'?(totalUnits>=0?'+':'')+totalUnits.toFixed(1)+'u':(totalDollars>=0?'+':'-')+'$'+Math.abs(totalDollars).toFixed(0)}</Text><Text style={styles.statKey}>{trackingMode==='units'?'Units':'P/L'}</Text></View>
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

            {gamesSport==='MLB'&&(
              <View style={{backgroundColor:'rgba(255,184,0,0.08)',borderRadius:12,padding:16,marginBottom:14,borderWidth:1,borderColor:'rgba(255,184,0,0.25)',alignItems:'center'}}>
                <Text style={{fontSize:28,marginBottom:6}}>⚾</Text>
                <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14,marginBottom:4}}>MLB MODEL COMING MARCH 27</Text>
                <Text style={{color:'#7a92a8',fontSize:12,textAlign:'center',lineHeight:18}}>Opening Day. Statcast-powered model with park factors, umpire tendencies, pitcher rest, and weather data.</Text>
                <View style={{marginTop:10,backgroundColor:'rgba(255,184,0,0.15)',borderRadius:8,paddingHorizontal:12,paddingVertical:5}}>
                  <Text style={{color:HRB_COLOR,fontSize:11,fontWeight:'700'}}>🔬 CUTTING EDGE ANALYSIS INCOMING</Text>
                </View>
              </View>
            )}
            {gamesLoading?(<View style={{alignItems:'center',paddingTop:60}}><ActivityIndicator size="large" color={HRB_COLOR}/><Text style={{color:'#7a92a8',marginTop:12}}>Loading games...</Text></View>):
            gamesData.length===0?(<View style={{alignItems:'center',paddingTop:60}}><Text style={{fontSize:40}}>{SPORT_EMOJI[gamesSport]}</Text><Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>No {gamesSport} games {gamesDay}.{'\n'}Try a different sport or day.</Text></View>):(
              <>
                 <Text style={styles.sectionLabel}>{gamesData.length} GAMES — {gamesDay.toUpperCase()}</Text>
                {gamesData.filter((game) =>
                  gamesSearch==='' ||
                  game.away_team.toLowerCase().includes(gamesSearch.toLowerCase()) ||
                  game.home_team.toLowerCase().includes(gamesSearch.toLowerCase())
                ).map((game, i) => {
                  const summary=getGameSummary(game);
                  const gameTime=new Date(game.commence_time);
                  const isLive=new Date()>gameTime&&new Date()<new Date(gameTime.getTime()+3*60*60*1000);
                  //console.log('HRB search - bookmaker keys:', game.bookmakers.map(bm=>bm.key));
                  const hrbLine=getHRBLine(game);
                  const hrbSpread=hrbLine&&hrbLine.spread?hrbLine.spread[0]:null;
                  const hrbTotal=hrbLine&&hrbLine.total?hrbLine.total[0]:null;
                  return(
                    <TouchableOpacity key={i} style={styles.gameCard} onPress={()=>openGameDetail(game)} activeOpacity={0.8}>
                      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
                        <Text style={{fontSize:11,color:'#7a92a8',fontWeight:'600'}}>{gameTime.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'})}</Text>
                        {isLive?(<View style={{flexDirection:'row',alignItems:'center',gap:4,backgroundColor:'rgba(255,77,109,0.15)',paddingHorizontal:8,paddingVertical:3,borderRadius:20}}><View style={{width:6,height:6,borderRadius:3,backgroundColor:'#ff4d6d'}}/><Text style={{color:'#ff4d6d',fontSize:11,fontWeight:'700'}}>LIVE</Text></View>):
                        (<View style={[styles.pill,{backgroundColor:'rgba(0,153,255,0.15)'}]}><Text style={{color:'#0099ff',fontSize:11,fontWeight:'700'}}>{gamesSport}</Text></View>)}
                      </View>
                       {(()=>{
                          if(gamesSport==='NCAAB' && !bartData.length) return null;
                  const isLive = new Date(game.commence_time) <= new Date();
                  if(isLive) return null;
                  const ss = getSweatScoreForGame(game, gamesSport);
                  if(!ss) return null;
                        const tier = getSweatTier(ss.total);
                        return(
                          <View style={{flexDirection:'row',alignItems:'center',gap:6,marginBottom:8}}>
                            <View style={{paddingHorizontal:10,paddingVertical:4,borderRadius:20,backgroundColor:tier.color+'22',borderWidth:1,borderColor:tier.color,flexDirection:'row',alignItems:'center',gap:4}}>
                              <Text style={{color:tier.color,fontWeight:'800',fontSize:13}}>{ss.total}</Text>
                              <Text style={{color:tier.color,fontSize:10,fontWeight:'700'}}>SWEAT</Text>
                            </View>
                            <Text style={{color:tier.color,fontSize:11,fontWeight:'600'}}>{tier.label}</Text>
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
                      {hrbLine?(
                        <View style={{backgroundColor:'rgba(255,184,0,0.07)',borderRadius:10,padding:10,marginBottom:8,borderWidth:1,borderColor:'rgba(255,184,0,0.25)'}}>
                          <Text style={{color:HRB_COLOR,fontSize:10,fontWeight:'800',marginBottom:6}}>🎸 HARD ROCK BET</Text>
                          <View style={{flexDirection:'row',gap:6}}>
                            {hrbSpread&&<View style={{flex:1,alignItems:'center'}}><Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>SPREAD</Text><Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:13,marginTop:2}}>{hrbSpread.name.split(' ').pop()} {hrbSpread.point>0?'+':''}{hrbSpread.point}</Text><Text style={{color:'#7a92a8',fontSize:10}}>{hrbSpread.price>0?'+':''}{hrbSpread.price}</Text></View>}
                            {hrbTotal&&<View style={{flex:1,alignItems:'center'}}><Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>TOTAL</Text><Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:13,marginTop:2}}>O/U {hrbTotal.point}</Text><Text style={{color:'#7a92a8',fontSize:10}}>{hrbTotal.price>0?'+':''}{hrbTotal.price}</Text></View>}
                            {hrbLine.ml&&hrbLine.ml[0]&&<View style={{flex:1,alignItems:'center'}}><Text style={{color:'#4a6070',fontSize:9,fontWeight:'700'}}>ML</Text><Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:13,marginTop:2}}>{hrbLine.ml[0].price>0?'+':''}{hrbLine.ml[0].price}</Text><Text style={{color:'#7a92a8',fontSize:10}}>{game.away_team.split(' ').pop()}</Text></View>}
                          </View>
                        </View>
                      ):(
                        <View style={{flexDirection:'row',gap:6,marginBottom:8}}>
                          <View style={styles.oddsQuickChip}><Text style={styles.oddsQuickLabel}>SPREAD</Text><Text style={styles.oddsQuickVal}>{summary.spread}</Text></View>
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

        {activeTab==='trends'&&(
          <View>
            <Text style={styles.pageTitle}>Trends & EV</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:14}}>
              <View style={{flexDirection:'row',gap:6}}>
                {[{id:'ev',label:'⚡ +EV'},{id:'propjerry',label:'🧠 Prop Jerry'},{id:'mytrends',label:'📊 My Trends'},{id:'clv',label:'📈 CLV'}].map(t=>(
                  <TouchableOpacity key={t.id} style={[styles.chipBtn,trendsTab===t.id&&styles.chipBtnActive]} onPress={()=>setTrendsTab(t.id)}>
                    <Text style={[styles.chipTxt,trendsTab===t.id&&styles.chipTxtActive]}>{t.label}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            </ScrollView>

            {trendsTab==='ev'&&(
              <View>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:14}}>
                  <View style={{flexDirection:'row',gap:6}}>{SPORTS.map(s=>(<TouchableOpacity key={s} style={[styles.chipBtn,evSport===s&&styles.chipBtnActive]} onPress={()=>setEvSport(s)}><Text style={[styles.chipTxt,evSport===s&&styles.chipTxtActive]}>{SPORT_EMOJI[s]} {s}</Text></TouchableOpacity>))}</View>
                </ScrollView>
                <View style={{backgroundColor:'rgba(255,184,0,0.07)',borderRadius:12,padding:12,marginBottom:14,borderWidth:1,borderColor:'rgba(255,184,0,0.25)'}}>
                  <Text style={{color:HRB_COLOR,fontWeight:'700',fontSize:12,marginBottom:4}}>🎸 HARD ROCK BET — +EV TRACKER</Text>
                  <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Hard Rock opportunities pinned to top. We calculate vig-free market probability and flag when Hard Rock offers better odds than the market.</Text>
                </View>
                {evLoading?(<View style={{alignItems:'center',paddingTop:40}}><ActivityIndicator size="large" color={HRB_COLOR}/><Text style={{color:'#7a92a8',marginTop:12}}>Scanning for +EV...</Text></View>):
                evData.length===0?(<View style={{alignItems:'center',paddingTop:40}}><Text style={{fontSize:32}}>⚡</Text><Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>No +EV opportunities found.{'\n'}Pull to refresh.</Text></View>):(
                  <>
                    <Text style={styles.sectionLabel}>{evData.length} +EV OPPORTUNITIES</Text>
                    {evData.map((opp,i)=>(
                      <View key={i} style={[styles.card,{marginBottom:10,borderLeftWidth:3,borderLeftColor:opp.isHRB?HRB_COLOR:'#00e5a0'}]}>
                        {opp.isHRB&&<View style={{backgroundColor:'rgba(255,184,0,0.1)',borderRadius:8,paddingHorizontal:10,paddingVertical:4,marginBottom:8,alignSelf:'flex-start',borderWidth:1,borderColor:'rgba(255,184,0,0.3)'}}><Text style={{color:HRB_COLOR,fontSize:10,fontWeight:'800'}}>🎸 HARD ROCK BET EDGE</Text></View>}
                        <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start',marginBottom:8}}>
                          <View style={{flex:1}}><Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14}}>{opp.pick}</Text><Text style={{color:'#7a92a8',fontSize:12,marginTop:2}}>{opp.game}</Text></View>
                          <View style={{backgroundColor:opp.isHRB?'rgba(255,184,0,0.15)':'rgba(0,229,160,0.15)',borderRadius:8,paddingHorizontal:10,paddingVertical:4,borderWidth:1,borderColor:opp.isHRB?HRB_COLOR:'#00e5a0'}}>
                            <Text style={{color:opp.isHRB?HRB_COLOR:'#00e5a0',fontWeight:'800',fontSize:14}}>+{opp.ev}% EV</Text>
                          </View>
                        </View>
                        <View style={{flexDirection:'row',gap:6,flexWrap:'wrap'}}>
                          <View style={[styles.metaChip,{borderColor:opp.isHRB?HRB_COLOR:'#1f2d3d'}]}><Text style={{color:opp.isHRB?HRB_COLOR:'#7a92a8',fontSize:11,fontWeight:opp.isHRB?'700':'400'}}>{opp.book}</Text></View>
                          <View style={styles.metaChip}><Text style={{color:'#e8f0f8',fontSize:11}}>{opp.odds>0?'+':''}{opp.odds}</Text></View>
                          <View style={styles.metaChip}><Text style={{color:'#7a92a8',fontSize:11}}>{opp.market}</Text></View>
                          <View style={styles.metaChip}><Text style={{color:'#7a92a8',fontSize:11}}>Mkt: {opp.marketProb}%</Text></View>
                        </View>
                        <TouchableOpacity style={[styles.btnPrimary,{marginTop:10,paddingVertical:8,backgroundColor:opp.isHRB?HRB_COLOR:'#00e5a0'}]} onPress={()=>{setForm({matchup:opp.game,pick:opp.pick,sport:evSport,type:opp.market,odds:String(opp.odds),units:'',book:opp.book,result:'Pending'});setModalVisible(true);}}>
                          <Text style={styles.btnPrimaryText}>+ Log This Pick</Text>
                        </TouchableOpacity>
                      </View>
                    ))}
                  </>
                )}
              </View>
            )}

             {trendsTab==='propjerry'&&(
  <View>
    <View style={{backgroundColor:'rgba(255,184,0,0.07)',borderRadius:12,padding:12,marginBottom:14,borderWidth:1,borderColor:'rgba(255,184,0,0.25)'}}>
      <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start'}}>
  <View style={{flex:1}}>
    <Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:14,marginBottom:4}}>🎤 PROP JERRY</Text>
    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Pure EV + market consensus. No simulated data. Jerry finds the real edges.</Text>
  </View>
  <TouchableOpacity onPress={()=>fetchPropJerry(propJerrySport)} style={{alignItems:'center',gap:3}}>
    <Text style={{fontSize:18}}>🔄</Text>
    <Text style={{color:'#4a6070',fontSize:9}}>{propJerryLastUpdate ? Math.floor((new Date()-propJerryLastUpdate)/60000)+'m ago' : 'tap to load'}</Text>
  </TouchableOpacity>
</View>
    </View>
   
    {/* Sport Selector - no NCAAB */}
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:14}}>
      <View style={{flexDirection:'row',gap:6}}>
        {['NBA','NFL','NHL','MLB'].map(s=>(
          <TouchableOpacity key={s} style={[styles.chipBtn,propJerrySport===s&&styles.chipBtnActive]} onPress={()=>{setPropJerrySport(s);fetchPropJerry(s);}}>
            <Text style={[styles.chipTxt,propJerrySport===s&&styles.chipTxtActive]}>{SPORT_EMOJI[s]} {s}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </ScrollView>

    {propJerryLoading?(
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
              <Text style={{color:'#7a92a8',fontSize:12,fontStyle:'italic',lineHeight:18}}>{prop.Jerry}</Text>
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
                    setActiveTab('parlay');
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
              <Text style={{color:'#7a92a8',fontSize:12,fontStyle:'italic',lineHeight:18}}>{prop.Jerry}</Text>
            </View>
          </View>
        ))}
      </>
    )}
  </View>
)}


            {trendsTab==='mytrends'&&(
              <View>
                {trends.total===0?(<View style={{alignItems:'center',paddingTop:40}}><Text style={{fontSize:32}}>📊</Text><Text style={{color:'#7a92a8',marginTop:12,fontSize:14,textAlign:'center'}}>Log some settled bets to see your trends!</Text></View>):(
                  <>
                    <View style={{flexDirection:'row',gap:8,marginBottom:14}}>
                      <View style={[styles.statBox,{flex:2,borderColor:trends.streakType==='Win'?'rgba(0,229,160,0.3)':'rgba(255,77,109,0.3)'}]}><Text style={{fontSize:22,fontWeight:'800',color:trends.streakType==='Win'?'#00e5a0':'#ff4d6d'}}>{trends.streak}</Text><Text style={{fontSize:10,color:'#7a92a8',fontWeight:'600',marginTop:2}}>{trends.streakType==='Win'?'WIN':'LOSS'} STREAK</Text></View>
                      {trends.best&&<View style={[styles.statBox,{flex:3,borderColor:'rgba(0,229,160,0.3)'}]}><Text style={{fontSize:12,fontWeight:'800',color:'#00e5a0'}}>🔥 {trends.best.label}</Text><Text style={{fontSize:10,color:'#7a92a8',marginTop:2}}>{trends.best.w}-{trends.best.l} • {trends.best.pct}%</Text></View>}
                      {trends.worst&&<View style={[styles.statBox,{flex:3,borderColor:'rgba(255,77,109,0.3)'}]}><Text style={{fontSize:12,fontWeight:'800',color:'#ff4d6d'}}>🧊 {trends.worst.label}</Text><Text style={{fontSize:10,color:'#7a92a8',marginTop:2}}>{trends.worst.w}-{trends.worst.l} • {trends.worst.pct}%</Text></View>}
                    </View>
                    {[{title:'BY SPORT',data:trends.bySport},{title:'BY BET TYPE',data:trends.byType},{title:'BY SPORTSBOOK',data:trends.byBook}].map((section,si)=>(
                      <View key={si}>
                        <Text style={styles.sectionLabel}>{section.title}</Text>
                        <View style={styles.card}>
                          {section.data.map((item,i)=>(
                            <View key={i} style={[styles.teamRow,{paddingVertical:10}]}>
                              <View style={{flex:1}}><Text style={[styles.teamName,item.label===HRB&&{color:HRB_COLOR}]}>{item.label===HRB?'🎸 ':SPORT_EMOJI[item.label]||''} {item.label}</Text><Text style={styles.teamSub}>{item.w}W - {item.l}L</Text></View>
                              <View style={{alignItems:'flex-end'}}>
                                <Text style={{color:parseFloat(item.pct)>=50?'#00e5a0':'#ff4d6d',fontWeight:'800',fontSize:15}}>{item.pct}%</Text>
                                <View style={{width:80,height:4,backgroundColor:'#1f2d3d',borderRadius:2,marginTop:4,overflow:'hidden'}}><View style={{height:'100%',width:item.pct+'%',backgroundColor:item.label===HRB?HRB_COLOR:parseFloat(item.pct)>=50?'#00e5a0':'#ff4d6d',borderRadius:2}}/></View>
                              </View>
                            </View>
                          ))}
                        </View>
                      </View>
                    ))}
                  </>
                )}
              </View>
            )}

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
              {[{id:'props',label:'🎯 Props'},{id:'players',label:'📊 Players'},{id:'teams',label:'🎤 Jerry'}].map(t=>(
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
  const settled = jerryHistory.filter(h=>h.result==='Win'||h.result==='Loss');
  const wins = settled.filter(h=>h.result==='Win').length;
  const losses = settled.filter(h=>h.result==='Loss').length;
  const winRate = wins+losses>0?((wins/(wins+losses))*100).toFixed(0):'—';
  const byGrade = ['A','B','C','D'].map(g=>{
    const gradeSettled = settled.filter(h=>h.grade===g);
    const gWins = gradeSettled.filter(h=>h.result==='Win').length;
    const gLosses = gradeSettled.filter(h=>h.result==='Loss').length;
    const pending = jerryHistory.filter(h=>h.grade===g&&h.result==='Pending').length;
    return{grade:g, wins:gWins, losses:gLosses, pending, pct:gWins+gLosses>0?((gWins/(gWins+gLosses))*100).toFixed(0):'—'};
  });
  const bySport = ['NBA','NFL','NHL','MLB'].map(s=>{
    const sportSettled = settled.filter(h=>h.sport===s);
    const sWins = sportSettled.filter(h=>h.result==='Win').length;
    const sLosses = sportSettled.filter(h=>h.result==='Loss').length;
    return{sport:s, wins:sWins, losses:sLosses, pct:sWins+sLosses>0?((sWins/(sLosses+sWins))*100).toFixed(0):'—'};
  }).filter(s=>s.wins+s.losses>0);
  const aGrades = jerryHistory.filter(h=>h.grade==='A');
  const aSettled = aGrades.filter(h=>h.result==='Win'||h.result==='Loss');
  const aWins = aSettled.filter(h=>h.result==='Win').length;
  const aLosses = aSettled.filter(h=>h.result==='Loss').length;
  return(
    <View>
      {/* Hero Record */}
      <View style={[styles.hero,{flexDirection:'column',alignItems:'stretch',marginBottom:14}]}>
        <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
          <View>
            <Text style={{color:'#7a92a8',fontSize:12,fontWeight:'600'}}>JERRY'S RECORD</Text>
            <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:32}}>{wins}-{losses}</Text>
            <Text style={{color:'#7a92a8',fontSize:12,marginTop:2}}>{winRate}% hit rate • {jerryHistory.filter(h=>h.result==='Pending').length} pending</Text>
          </View>
          <View style={{alignItems:'center'}}>
            <View style={{width:72,height:72,borderRadius:36,borderWidth:2.5,borderColor:'#00e5a0',alignItems:'center',justifyContent:'center',backgroundColor:'rgba(0,229,160,0.1)'}}>
              <Text style={{color:'#00e5a0',fontWeight:'800',fontSize:22}}>{winRate}%</Text>
            </View>
            <Text style={{color:'#7a92a8',fontSize:10,marginTop:4}}>HIT RATE</Text>
          </View>
        </View>
        {/* A Grade highlight */}
        <View style={{backgroundColor:'rgba(0,229,160,0.1)',borderRadius:12,padding:12,borderWidth:1,borderColor:'rgba(0,229,160,0.3)',flexDirection:'row',justifyContent:'space-between',alignItems:'center'}}>
          <View>
            <Text style={{color:'#00e5a0',fontWeight:'800',fontSize:13}}>🔥 A GRADE RECORD</Text>
            <Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>{aGrades.length} total • {aGrades.filter(h=>h.result==='Pending').length} pending</Text>
          </View>
          <Text style={{color:'#00e5a0',fontWeight:'800',fontSize:28}}>{aWins}-{aLosses}</Text>
        </View>
      </View>
      {/* Grade Breakdown */}
      <Text style={styles.sectionLabel}>BY GRADE</Text>
      <View style={[styles.card,{marginBottom:14}]}>
        {byGrade.map((g,i)=>{
          const gradeColors = {'A':'#00e5a0','B':'#FFB800','C':'#0099ff','D':'#ff4d6d'};
          const color = gradeColors[g.grade];
          return(
            <View key={i} style={{flexDirection:'row',alignItems:'center',paddingVertical:10,borderBottomWidth:i<3?1:0,borderBottomColor:'#1f2d3d'}}>
              <View style={{width:36,height:36,borderRadius:18,borderWidth:2,borderColor:color,alignItems:'center',justifyContent:'center',backgroundColor:color+'20',marginRight:12}}>
                <Text style={{color:color,fontWeight:'900',fontSize:16}}>{g.grade}</Text>
              </View>
              <View style={{flex:1}}>
                <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14}}>{g.wins}-{g.losses}</Text>
                <Text style={{color:'#4a6070',fontSize:11,marginTop:2}}>{g.pending} pending</Text>
              </View>
              <Text style={{color:parseFloat(g.pct)>=50?'#00e5a0':'#ff4d6d',fontWeight:'800',fontSize:16}}>{g.pct}{g.pct!=='—'?'%':''}</Text>
            </View>
          );
        })}
      </View>
      {/* By Sport */}
      {bySport.length>0&&(
        <>
          <Text style={styles.sectionLabel}>BY SPORT</Text>
          <View style={[styles.card,{marginBottom:14}]}>
            {bySport.map((s,i)=>(
              <View key={i} style={{flexDirection:'row',alignItems:'center',paddingVertical:10,borderBottomWidth:i<bySport.length-1?1:0,borderBottomColor:'#1f2d3d'}}>
                <Text style={{fontSize:20,marginRight:12}}>{SPORT_EMOJI[s.sport]||'🎯'}</Text>
                <View style={{flex:1}}>
                  <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14}}>{s.sport}</Text>
                  <Text style={{color:'#4a6070',fontSize:11,marginTop:2}}>{s.wins}-{s.losses}</Text>
                </View>
                <Text style={{color:parseFloat(s.pct)>=50?'#00e5a0':'#ff4d6d',fontWeight:'800',fontSize:16}}>{s.pct}{s.pct!=='—'?'%':''}</Text>
              </View>
            ))}
          </View>
        </>
      )}
      {/* Recent Jerry Picks */}
      <Text style={styles.sectionLabel}>RECENT JERRY PICKS</Text>
      {jerryHistory.length===0?(
        <View style={{alignItems:'center',paddingVertical:40}}>
          <Text style={{fontSize:32}}>🎤</Text>
          <Text style={{color:'#7a92a8',fontSize:13,marginTop:8,textAlign:'center'}}>No Jerry picks yet.{'\n'}Head to Prop Jerry to start tracking!</Text>
        </View>
      ):(
        jerryHistory.filter(h=>h.grade!=='D').slice(0,20).map((h,i)=>{
          const gradeColors = {'A':'#00e5a0','B':'#FFB800','C':'#0099ff','D':'#ff4d6d'};
          const color = gradeColors[h.grade];
          return(
            <View key={i} style={[styles.betCard,{borderLeftColor:color,marginBottom:8}]}>
              <View style={{flexDirection:'row',justifyContent:'space-between',alignItems:'flex-start'}}>
                <View style={{flex:1,marginRight:12}}>
                  <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14}}>{h.player}</Text>
                  <Text style={{color:'#7a92a8',fontSize:12,marginTop:2}}>{h.bestSide} {h.bestLine} • {h.market}</Text>
                  <Text style={{color:'#4a6070',fontSize:11,marginTop:2}}>{h.game}</Text>
                </View>
                <View style={{alignItems:'center',gap:4}}>
                  <View style={{width:36,height:36,borderRadius:18,borderWidth:2,borderColor:color,alignItems:'center',justifyContent:'center',backgroundColor:color+'20'}}>
                    <Text style={{color:color,fontWeight:'900',fontSize:16}}>{h.grade}</Text>
                  </View>
                  <TouchableOpacity onPress={()=>{
                    const updated = jerryHistory.map((item)=>item.id===h.id?{...item,result:item.result==='Pending'?'Win':item.result==='Win'?'Loss':'Pending'}:item);
                    setJerryHistory(updated);
                    AsyncStorage.setItem(JERRY_HISTORY_KEY, JSON.stringify(updated));
                  }}>
                    <Text style={{color:h.result==='Win'?'#00e5a0':h.result==='Loss'?'#ff4d6d':'#4a6070',fontSize:11,fontWeight:'700'}}>{h.result}</Text>
                  </TouchableOpacity>
                </View>
              </View>
              <View style={{flexDirection:'row',gap:6,marginTop:8,flexWrap:'wrap'}}>
                <View style={styles.metaChip}><Text style={{color:'#7a92a8',fontSize:11}}>{h.sport}</Text></View>
                <View style={styles.metaChip}><Text style={{color:'#00e5a0',fontSize:11}}>+{h.ev.toFixed(1)}% EV</Text></View>
                <View style={styles.metaChip}><Text style={{color:'#7a92a8',fontSize:11}}>{h.bestOdds>0?'+':''}{h.bestOdds}</Text></View>
                <View style={styles.metaChip}><Text style={{color:'#7a92a8',fontSize:11}}>{h.date}</Text></View>
              </View>
            </View>
          );
        })
      )}
      <View style={{height:20}}/>
    </View>
  );
})()}
      </View>
        )}
        {activeTab==='parlay'&&(
          <View>
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
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.bottomNav}>
          {[{id:'home',icon:'📊',label:'Home'},{id:'picks',icon:'🎯',label:'Picks'},{id:'games',icon:'🏟',label:'Games'},{id:'trends',icon:'⚡',label:'Trends'},{id:'odds',icon:'💰',label:'Odds'},{id:'stats',icon:'📈',label:'Stats'},{id:'parlay',icon:'🎰',label:'Parlay'}].map(tab=>(
            <TouchableOpacity key={tab.id} style={styles.tabItem} onPress={()=>setActiveTab(tab.id)}>
              <Text style={styles.tabIcon}>{tab.icon}</Text>
              {tab.id==='parlay'&&parlayLegs.length>0?(
                <View style={{position:'relative'}}><Text style={[styles.tabLabel,activeTab===tab.id&&{color:HRB_COLOR}]}>{tab.label}</Text><View style={styles.tabBadge}><Text style={styles.tabBadgeText}>{parlayLegs.length}</Text></View></View>
              ):(<Text style={[styles.tabLabel,activeTab===tab.id&&{color:HRB_COLOR}]}>{tab.label}</Text>)}
            </TouchableOpacity>
          ))}
        </ScrollView>
      </View>

      {selectedGame&&(
        <Modal visible={gameDetailModal} animationType="slide" transparent>
          <View style={styles.modalOverlay}>
            <View style={[styles.modalSheet,{maxHeight:'92%'}]}>
              <View style={styles.modalHandle}/>
              <Text style={[styles.modalTitle, {fontSize:18}]} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.6}>{stripMascot(selectedGame.away_team)} @ {stripMascot(selectedGame.home_team)}</Text>
              <Text style={{color:'#7a92a8',fontSize:12,marginTop:-10,marginBottom:16}}>{new Date(selectedGame.commence_time).toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'})} • {new Date(selectedGame.commence_time).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'})}</Text>
              <ScrollView showsVerticalScrollIndicator={false}>
                
                {(()=>{
                  const isLive = new Date(selectedGame.commence_time) <= new Date();
                  if(isLive) return null;
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
                        {hrbLine.spread&&hrbLine.spread[0]&&<View style={{flex:1,backgroundColor:'#151c24',borderRadius:10,padding:10,alignItems:'center'}}><Text style={{color:'#4a6070',fontSize:10,fontWeight:'700'}}>SPREAD</Text><Text style={{color:HRB_COLOR,fontWeight:'800',fontSize:16,marginTop:4}}>{hrbLine.spread[0].name.split(' ').pop()} {hrbLine.spread[0].point>0?'+':''}{hrbLine.spread[0].point}</Text><Text style={{color:'#7a92a8',fontSize:11,marginTop:2}}>{hrbLine.spread[0].price>0?'+':''}{hrbLine.spread[0].price}</Text></View>}
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
                      {spreadMkt&&spreadMkt.outcomes&&spreadMkt.outcomes.map((outcome,j)=>(
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
        <View style={styles.modalOverlay}>
          <View style={[styles.modalSheet,{maxHeight:'50%'}]}>
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
        </View>
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
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Every game graded 0-100 based on market efficiency, sharp money movement, line variance, and our analytics model. 85+ is a Prime Sweat — our highest confidence signal.</Text>
                  </View>
                  <View style={{borderLeftWidth:3,borderLeftColor:'#00e5a0',paddingLeft:10}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:4}}>🎤 Prop Jerry</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>AI-powered prop analyst grading every player prop A through D based on real expected value math. A grades are elite edges. Jerry tells you what he sees in plain English.</Text>
                  </View>
                  <View style={{borderLeftWidth:3,borderLeftColor:'#0099ff',paddingLeft:10}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:4}}>📊 Line Movement</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>See how lines move across all major books. Sharp money moves lines — we show you where Hard Rock stands vs the market so you always get the best number.</Text>
                  </View>
                  <View style={{borderLeftWidth:3,borderLeftColor:'#ffd166',paddingLeft:10}}>
                    <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:13,marginBottom:4}}>📈 ROI Tracker</Text>
                    <Text style={{color:'#7a92a8',fontSize:12,lineHeight:18}}>Log every pick and track your real performance. Win rate, units, profit/loss — all in one place. The only way to know if you have an edge is to track it honestly.</Text>
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
              <View style={[styles.card,{marginBottom:12}]}>
                <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14,marginBottom:12}}>🔒 Privacy Policy</Text>
                <Text style={{color:'#7a92a8',fontSize:12,lineHeight:20}}>
                  Last updated March 2026{'\n\n'}
                  Note: "Hard Rock Bet" and the guitar logo are trademarks of the Seminole Tribe of Florida/Hard Rock Digital. Used for informational purposes only.  We collect only the data you voluntarily enter: your bet logs, favorite sports, and unit size. No personal financial information, no location tracking beyond device settings, no data selling or sharing with third parties except as required for Hard Rock Bet affiliate tracking (anonymized volume only).{'\n\n'}
                  You can delete all your data anytime below.{'\n\n'}
                  We use TheOddsAPI, BartTorvik, and BDL solely for public sports data. Your bet history never leaves your device unless you choose to share a slip in the future Locker Room feature.{'\n\n'}
                  Contact: [sweatlockerofficial@thesweatlocker.net] for any privacy questions.
                </Text>
              </View>
                {/* Terms of Service */}
              <View style={[styles.card,{marginBottom:12}]}>
                <Text style={{color:'#e8f0f8',fontWeight:'700',fontSize:14,marginBottom:12}}>📋 Terms of Service</Text>
                <Text style={{color:'#7a92a8',fontSize:12,lineHeight:20}}>
                  Last Updated: March 5, 2026{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>1. Acceptance of Terms{'\n'}</Text>
                  By using The Sweat Locker you agree to these Terms. If you do not agree, do not use the App.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>2. Eligibility{'\n'}</Text>
                  You must be at least 21 years of age or the legal sports betting age in your jurisdiction. Sports betting may not be legal where you are — know your local laws.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>3. Nature of the App{'\n'}</Text>
                  The Sweat Locker is an analytics and information tool. Nothing in the App constitutes financial, legal, or gambling advice. All information is for entertainment and informational purposes only.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>4. No Guarantee of Results{'\n'}</Text>
                  The Sweat Score, Prop Jerry grades, EV calculations, and all analytical tools are probabilistic in nature. You may lose money betting on sports regardless of information provided by this App.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>5. Data Accuracy{'\n'}</Text>
                  We source data from The Odds API, BartTorvik, and Ball Don't Lie. We make no warranty that data is complete, accurate, or current. Always verify with your sportsbook before placing a bet.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>6. Responsible Gambling{'\n'}</Text>
                  If you or someone you know has a gambling problem, contact the National Problem Gambling Helpline: 1-800-522-4700 or ncpgambling.org.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>7. Intellectual Property{'\n'}</Text>
                  All content and features including Sweat Score™ and Prop Jerry™ are the intellectual property of The Sweat Locker. Unauthorized reproduction is prohibited.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>8. Limitation of Liability{'\n'}</Text>
                  To the fullest extent permitted by law, The Sweat Locker and its developers shall not be liable for any damages arising from your use of the App, including gambling losses.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>9. Governing Law{'\n'}</Text>
                  These Terms are governed by the laws of the State of Florida.{'\n\n'}
                  <Text style={{color:'#e8f0f8',fontWeight:'700'}}>10. Contact{'\n'}</Text>
                  sweatlockerofficial@thesweatlocker.net
                </Text>
              </View>
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
          <Text style={{fontSize:48,marginBottom:16}}>🔞</Text>
          <Text style={{color:'#e8f0f8',fontWeight:'800',fontSize:28,textAlign:'center',marginBottom:8}}>You must be 21+</Text>
          <Text style={{color:'#7a92a8',fontSize:15,textAlign:'center',lineHeight:24,marginBottom:32}}>The Sweat Locker is intended for users 18 years of age or older. By continuing you confirm you meet this requirement.</Text>
          <TouchableOpacity style={{backgroundColor:HRB_COLOR,borderRadius:14,paddingVertical:16,paddingHorizontal:40,marginBottom:12,width:'100%',alignItems:'center'}} onPress={()=>{setAgeGateVisible(false);setOnboardingVisible(true);}}>
            <Text style={{color:'#000',fontWeight:'800',fontSize:16}}>I am 18 or older — Continue</Text>
          </TouchableOpacity>
          <TouchableOpacity style={{paddingVertical:12}} onPress={()=>setAgeGateVisible(false)}>
            <Text style={{color:'#4a6070',fontSize:13,textAlign:'center'}}>I am under 21 — Exit</Text>
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
          <View style={styles.modalSheet}>
            <View style={styles.modalHandle}/>
            <Text style={styles.modalTitle}>Log New Pick</Text>
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
              <View style={{flex:1}}><Text style={styles.fieldLabel}>Units</Text><TextInput style={styles.input} placeholder="1" placeholderTextColor="#4a6070" value={form.units} onChangeText={t=>setForm({...form,units:t})} keyboardType="numeric" returnKeyType="done"/></View>
            </View>
            <Text style={styles.fieldLabel}>Sportsbook</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{marginBottom:12}}>
              <View style={{flexDirection:'row',gap:6}}>{BOOKS.map(b=>(<TouchableOpacity key={b} style={[styles.chipBtn,form.book===b&&{backgroundColor:'rgba(255,184,0,0.12)',borderColor:HRB_COLOR},form.book===b&&b!==HRB&&styles.chipBtnActive]} onPress={()=>setForm({...form,book:b})}><Text style={[styles.chipTxt,form.book===b&&{color:b===HRB?HRB_COLOR:'#00e5a0'}]}>{b===HRB?'🎸 ':''}{b}</Text></TouchableOpacity>))}</View>
            </ScrollView>
            <Text style={styles.fieldLabel}>Result</Text>
            <View style={{flexDirection:'row',gap:6,marginBottom:16}}>{RESULTS.map(r=>(<TouchableOpacity key={r} style={[styles.chipBtn,form.result===r&&styles.chipBtnActive]} onPress={()=>setForm({...form,result:r})}><Text style={[styles.chipTxt,form.result===r&&styles.chipTxtActive]}>{r}</Text></TouchableOpacity>))}</View>
            <TouchableOpacity style={styles.btnPrimary} onPress={saveBet}><Text style={styles.btnPrimaryText}>Save Pick ✓</Text></TouchableOpacity>
            <TouchableOpacity style={[styles.btnPrimary,{backgroundColor:'transparent',borderWidth:1,borderColor:'#1f2d3d',marginTop:8}]} onPress={()=>setModalVisible(false)}><Text style={[styles.btnPrimaryText,{color:'#7a92a8'}]}>Cancel</Text></TouchableOpacity>
          </View>
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