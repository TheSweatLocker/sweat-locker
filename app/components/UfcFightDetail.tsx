/**
 * UfcFightDetail — sport-agnostic modal body used when gamesSport === 'UFC'.
 *
 * Replaces GameDetailV2 for UFC (which is MLB team-sport oriented and
 * shows nothing but Jerry for fights). Renders the same tap-to-expand
 * body that lives on the UFC event card at the top of the sport-games
 * screen: pick header, H2H stats grid, method + round distribution bars,
 * full Jerry read, externals.
 *
 * All data is passed in from index.tsx — this component does no fetching.
 * The parent has ufcPicks, ufcJerryByGame, ufcFighterStats, ufcExternals
 * bulk-loaded already via fetchUfcEvent.
 */
import React from 'react';
import {View, Text, TouchableOpacity, ScrollView} from 'react-native';

// Local palette — matches app THEME. Kept inline so this component is
// self-contained (same pattern as GameDetailV2).
const T = {
  bg: '#0b1620',
  surface: '#12202c',
  text: '#e6edf3',
  textDim: '#9db1c3',
  textMuted: '#7a92a8',
  border: '#41586b',
  combat: '#ff6b3d',
  accent: '#5ea9e6',
  win: '#4ade80',
  loss: '#f87171',
  hrb: '#f5b800',
};

const TIER_COLOR: Record<string, string> = {
  PRIME: '#4ade80', STRONG: '#5ea9e6', LEAN: '#f5b800', PASS: '#7a92a8',
};

const scrubJerry = (t?: string | null): string => {
  if (!t) return '';
  return String(t).replace(/\[Auto-[^\]]*\]\s*/g, '').replace(/\s+/g, ' ').trim();
};

// Plain-english explanations of every MMA stat abbreviation. Tapped from
// the H2H grid — casuals don't know what SLpM/SApM/TD Def mean and the
// grid is useless to them without a translator.
const STAT_HELP: Record<string, string> = {
  'Record':   'Career MMA record: wins-losses-draws.',
  'SLpM':     'Significant Strikes Landed per Minute. Higher = more active + effective striker.',
  'Str Acc':  'Striking accuracy — % of significant strikes attempted that actually land. Higher = more precise.',
  'Str Def':  "Striking defense — % of opponent's significant strikes avoided. Higher = harder to hit.",
  'SApM':     'Significant Strikes Absorbed per Minute — how often they get hit. LOWER is better.',
  'TD Avg':   'Takedowns landed per 15 minutes (avg 3-round fight). Higher = more wrestling-heavy.',
  'TD Acc':   'Takedown accuracy — % of takedown attempts that succeed. Higher = better shot / setup.',
  'TD Def':   "Takedown defense — % of opponent's takedown attempts stuffed. Higher = harder to grapple.",
  'Sub Avg':  'Submission attempts per 15 minutes. High = active submission threat, not necessarily finishes.',
  'Finish %': 'Career wins that ended by KO/TKO or submission (not decision). Higher = finisher, not point-fighter.',
  'Stance':   'Orthodox (right-handed, left foot forward), Southpaw (left-handed, right foot forward), or Switch.',
  'Reach':    'Arm span in inches, fingertip to fingertip. Longer reach = jab + kick from safer distance.',
  'Height':   'Fighter height. Not always the reach advantage — check reach separately.',
  'Age':      'Fighter age. Prime for most is 27-33; older can mean slower recovery + longer camps to peak.',
};

type Props = {
  game: any;                                  // has home_team + away_team = fighter names
  ufcPicks: any[];                            // all picks for the event
  ufcJerryByGame: Record<string, any>;        // keyed by game_id
  ufcFighterStats: Record<string, any>;       // keyed by fighter last name (lower)
  ufcExternals: Record<string, any[]>;        // keyed by game_id
  onClose: () => void;
};

export default function UfcFightDetail({
  game, ufcPicks, ufcJerryByGame, ufcFighterStats, ufcExternals, onClose,
}: Props) {
  // Which stat label is currently showing its plain-english help row.
  // Null = none open. One at a time to keep the grid compact.
  const [helpStat, setHelpStat] = React.useState<string | null>(null);
  const fighter1 = game?.away_team || '';
  const fighter2 = game?.home_team || '';
  const f1last = String(fighter1).split(' ').pop()?.toLowerCase() || '';
  const f2last = String(fighter2).split(' ').pop()?.toLowerCase() || '';

  // Match this fight to a ufc_picks row via last-name pair (same logic
  // as the event-card list).
  const pick = (ufcPicks || []).find((p: any) => {
    const paLast = String(p.fighter_a || '').split(' ').pop()?.toLowerCase() || '';
    const pbLast = String(p.fighter_b || '').split(' ').pop()?.toLowerCase() || '';
    return (paLast === f1last && pbLast === f2last) || (paLast === f2last && pbLast === f1last);
  });

  const sideA = String(pick?.recommended_side || '').toLowerCase() === 'a';
  const pickName = pick ? (sideA ? pick.fighter_a : pick.fighter_b) : null;
  const winPct = pick ? Math.round((sideA ? pick.p_winner_a : (1 - (pick.p_winner_a || 0))) * 100) : null;
  const conv = pick?.conviction_winner;
  const tier = pick?.tier_winner as 'PRIME'|'STRONG'|'LEAN'|'PASS'|undefined;
  const tierColor = tier && TIER_COLOR[tier] ? TIER_COLOR[tier] : T.textMuted;

  const oddsDec = pick ? (sideA ? pick.odds_a_best : pick.odds_b_best) : null;
  const oddsAmerican = oddsDec
    ? (oddsDec >= 2 ? `+${Math.round((oddsDec - 1) * 100)}` : `-${Math.round(100 / (oddsDec - 1))}`)
    : null;
  const evPct = pick ? (sideA ? pick.ev_side_a : pick.ev_side_b) : null;
  const isSkip = pick?.ev_tier === 'SKIP';

  const kos = pick?.p_method_ko != null ? Math.round(pick.p_method_ko * 100) : null;
  const decs = pick?.p_method_dec != null ? Math.round(pick.p_method_dec * 100) : null;
  const subs = pick?.p_method_sub != null ? Math.round(pick.p_method_sub * 100) : null;
  const methodMax = pick?.edge_method || (kos != null && decs != null && subs != null
    ? (kos >= decs && kos >= subs ? 'KO' : decs >= subs ? 'DEC' : 'SUB')
    : null);
  const rounds = pick ? [
    {r:1, p:pick.p_round_1 || 0}, {r:2, p:pick.p_round_2 || 0}, {r:3, p:pick.p_round_3 || 0},
    {r:4, p:pick.p_round_4 || 0}, {r:5, p:pick.p_round_5 || 0},
  ] : [];
  const topRound = rounds.length ? rounds.reduce((a,b) => b.p > a.p ? b : a) : null;

  const jerryKey = pick ? `ufc_${pick.event_date}_${pick.fight_order}` : null;
  const jerry = jerryKey ? ufcJerryByGame[jerryKey] : null;
  const jerryFull = scrubJerry(jerry?.long_read) || scrubJerry(jerry?.short_read);

  const f1Stats = ufcFighterStats[f1last];
  const f2Stats = ufcFighterStats[f2last];
  const fightExternals = pick?.game_id ? (ufcExternals[pick.game_id] || []) : [];

  const hasStats = !!(f1Stats || f2Stats);
  const hasMethod = kos != null || decs != null || subs != null;
  const hasRounds = rounds.some(r => r.p > 0);
  const hasJerry = !!jerryFull;
  const hasExternals = fightExternals.length > 0;
  const nothing = !pick && !hasStats && !hasJerry && !hasExternals;

  const h2hRows: {label:string; f1:any; f2:any; higherWins?:boolean; fmt?:(v:any)=>string}[] = [
    {label:'Record',   f1:f1Stats?.record,          f2:f2Stats?.record},
    {label:'SLpM',     f1:f1Stats?.slpm,            f2:f2Stats?.slpm,            higherWins:true},
    {label:'Str Acc',  f1:f1Stats?.str_acc,         f2:f2Stats?.str_acc,         higherWins:true, fmt:(v)=>v!=null?`${v}%`:'—'},
    {label:'Str Def',  f1:f1Stats?.str_def,         f2:f2Stats?.str_def,         higherWins:true, fmt:(v)=>v!=null?`${v}%`:'—'},
    {label:'SApM',     f1:f1Stats?.sapm,            f2:f2Stats?.sapm,            higherWins:false},
    {label:'TD Avg',   f1:f1Stats?.td_avg,          f2:f2Stats?.td_avg,          higherWins:true},
    {label:'TD Acc',   f1:f1Stats?.td_acc,          f2:f2Stats?.td_acc,          higherWins:true, fmt:(v)=>v!=null?`${v}%`:'—'},
    {label:'TD Def',   f1:f1Stats?.td_def,          f2:f2Stats?.td_def,          higherWins:true, fmt:(v)=>v!=null?`${v}%`:'—'},
    {label:'Sub Avg',  f1:f1Stats?.sub_avg,         f2:f2Stats?.sub_avg,         higherWins:true},
    {label:'Finish %', f1:f1Stats?.finishing_rate,  f2:f2Stats?.finishing_rate,  higherWins:true, fmt:(v)=>v!=null?`${v}%`:'—'},
    {label:'Stance',   f1:f1Stats?.stance,          f2:f2Stats?.stance},
    {label:'Reach',    f1:f1Stats?.reach,           f2:f2Stats?.reach,           higherWins:true, fmt:(v)=>v!=null?`${v}"`:'—'},
    {label:'Height',   f1:f1Stats?.height,          f2:f2Stats?.height},
    {label:'Age',      f1:f1Stats?.age,             f2:f2Stats?.age,             higherWins:false},
  ];
  const validH2H = h2hRows.filter(r => r.f1 != null || r.f2 != null);

  return (
    <View style={{flex:1, backgroundColor: T.bg}}>
      {/* Header bar */}
      <View style={{flexDirection:'row', alignItems:'center', justifyContent:'space-between',
                    paddingHorizontal:16, paddingVertical:12, backgroundColor: T.surface,
                    borderBottomWidth:1, borderBottomColor: T.combat + '55'}}>
        <View style={{flex:1, paddingRight:12}}>
          <View style={{flexDirection:'row', alignItems:'center', gap:6, marginBottom:2}}>
            <Text style={{color: T.combat, fontSize:10, fontWeight:'800', letterSpacing:0.6}}>🥊 UFC FIGHT</Text>
            {tier && !isSkip && (
              <View style={{backgroundColor: tierColor + '26', paddingHorizontal:6, paddingVertical:1, borderRadius:4}}>
                <Text style={{color: tierColor, fontSize:9, fontWeight:'800', letterSpacing:0.4}}>{tier}</Text>
              </View>
            )}
            {isSkip && (
              <View style={{backgroundColor: T.textMuted + '18', paddingHorizontal:6, paddingVertical:1, borderRadius:4}}>
                <Text style={{color: T.textMuted, fontSize:9, fontWeight:'700', letterSpacing:0.4}}>SKIP</Text>
              </View>
            )}
            {evPct != null && evPct > 0 && (
              <View style={{backgroundColor: T.win + '20', paddingHorizontal:6, paddingVertical:1, borderRadius:4}}>
                <Text style={{color: T.win, fontSize:9, fontWeight:'800'}}>+{evPct.toFixed(1)}% EV</Text>
              </View>
            )}
          </View>
          <Text style={{color: T.text, fontSize:15, fontWeight:'700'}} numberOfLines={1}>
            {fighter1} <Text style={{color: T.textMuted}}>vs</Text> {fighter2}
          </Text>
        </View>
        <TouchableOpacity onPress={onClose} hitSlop={{top:12,bottom:12,left:12,right:12}}>
          <Text style={{color: T.textDim, fontSize:22, fontWeight:'700'}}>×</Text>
        </TouchableOpacity>
      </View>

      <ScrollView style={{flex:1}} contentContainerStyle={{padding:14, paddingBottom:36}}>
        {/* Pick + odds card */}
        {pickName && (
          <View style={{backgroundColor: T.combat + '14', borderRadius:10, padding:12, marginBottom:12,
                        borderWidth:1, borderColor: T.combat + '55', flexDirection:'row', alignItems:'center', justifyContent:'space-between'}}>
            <View style={{flex:1, paddingRight:12}}>
              <Text style={{color: T.textMuted, fontSize:9, fontWeight:'800', letterSpacing:0.5, marginBottom:3}}>MODEL PICK</Text>
              <Text style={{color: T.accent, fontSize:16, fontWeight:'800'}}>{pickName}</Text>
              <View style={{flexDirection:'row', alignItems:'center', gap:8, marginTop:5, flexWrap:'wrap'}}>
                {oddsAmerican && <Text style={{color: T.textDim, fontSize:12, fontWeight:'700'}}>{oddsAmerican}</Text>}
                {winPct != null && <Text style={{color: T.textDim, fontSize:11}}>{winPct}% to win</Text>}
                {methodMax && topRound && <Text style={{color: T.textMuted, fontSize:10}}>· {methodMax} · R{topRound.r} {Math.round(topRound.p*100)}%</Text>}
              </View>
            </View>
            {conv != null && (
              <View style={{minWidth:52, paddingHorizontal:10, paddingVertical:8,
                            backgroundColor: tierColor + '22', borderRadius:9,
                            borderWidth:1.5, borderColor: tierColor + '66', alignItems:'center'}}>
                <Text style={{color: tierColor, fontSize:18, fontWeight:'900'}}>{conv}</Text>
                <Text style={{color: tierColor + 'AA', fontSize:8, fontWeight:'700', letterSpacing:0.3}}>CONV</Text>
              </View>
            )}
          </View>
        )}

        {/* Diagnostic fallback when nothing loaded */}
        {nothing && (
          <View style={{padding:14, backgroundColor: T.border + '18', borderRadius:10,
                        borderWidth:1, borderColor: T.border + '44', marginBottom:12}}>
            <Text style={{color: T.textDim, fontSize:13, fontWeight:'700', marginBottom:6}}>
              No data loaded for this fight yet
            </Text>
            <Text style={{color: T.textMuted, fontSize:11, lineHeight:16}}>
              Model pick, fighter stats, Jerry read, and externals populate as the event approaches. Prelims often stay thin until Wednesday of fight week.
            </Text>
            <Text style={{color: T.textMuted, fontSize:10, marginTop:8, fontStyle:'italic'}}>
              Debug · pick={pick?'Y':'N'} stats={hasStats?'Y':'N'} jerry={hasJerry?'Y':'N'} ext={hasExternals?'Y':'N'} · looked up {f1last} / {f2last}
            </Text>
          </View>
        )}

        {/* Head-to-head stats grid — stat labels are tappable and
            reveal a plain-english explanation row below (STAT_HELP).
            Casuals don't know what SLpM or TD Def mean. */}
        {hasStats && (
          <View style={{marginBottom:14, backgroundColor: T.surface, borderRadius:10, padding:12}}>
            <View style={{flexDirection:'row', alignItems:'center', gap:6, marginBottom:8}}>
              <Text style={{color: T.combat, fontSize:11, fontWeight:'800', letterSpacing:0.6}}>
                HEAD-TO-HEAD
              </Text>
              <Text style={{color: T.textMuted, fontSize:9, fontWeight:'600', fontStyle:'italic'}}>
                tap any stat name to learn what it means
              </Text>
            </View>
            <View style={{flexDirection:'row', paddingBottom:6, borderBottomWidth:1, borderBottomColor: T.border + '55'}}>
              <Text style={{flex:1, color: T.text, fontSize:12, fontWeight:'700'}} numberOfLines={1}>{fighter1}</Text>
              <Text style={{width:80, color: T.textMuted, fontSize:10, fontWeight:'700', textAlign:'center', letterSpacing:0.3}}>STAT</Text>
              <Text style={{flex:1, color: T.text, fontSize:12, fontWeight:'700', textAlign:'right'}} numberOfLines={1}>{fighter2}</Text>
            </View>
            {validH2H.map((row, ri) => {
              const v1 = row.f1; const v2 = row.f2;
              const fmt = row.fmt || ((v: any) => v != null ? String(v) : '—');
              let adv: 1|2|null = null;
              if (row.higherWins != null && typeof v1 === 'number' && typeof v2 === 'number' && v1 !== v2) {
                adv = row.higherWins ? (v1 > v2 ? 1 : 2) : (v1 < v2 ? 1 : 2);
              }
              const help = STAT_HELP[row.label];
              const isHelpOpen = helpStat === row.label;
              const hasBorder = ri < validH2H.length - 1;
              return (
                <View key={ri} style={{borderBottomWidth: hasBorder && !isHelpOpen ? 1 : 0,
                                        borderBottomColor: T.border + '22'}}>
                  <View style={{flexDirection:'row', paddingVertical:5, alignItems:'center'}}>
                    <Text style={{flex:1, color: adv === 1 ? T.win : T.textDim, fontSize:12, fontWeight: adv === 1 ? '800' : '600'}}>{fmt(v1)}</Text>
                    {/* Tappable label. Underlined + info dot so the tap
                        affordance is obvious without shouting. */}
                    <TouchableOpacity
                      onPress={() => setHelpStat(isHelpOpen ? null : row.label)}
                      disabled={!help}
                      style={{width:80, alignItems:'center', paddingVertical:2}}
                      hitSlop={{top:6, bottom:6, left:6, right:6}}
                    >
                      <View style={{flexDirection:'row', alignItems:'center', gap:3}}>
                        <Text style={{
                          color: isHelpOpen ? T.combat : T.textMuted,
                          fontSize:10, fontWeight:'700', letterSpacing:0.3,
                          textDecorationLine: help ? 'underline' : 'none',
                          textDecorationStyle: 'dotted',
                          textDecorationColor: T.textMuted + '77',
                        }}>{row.label.toUpperCase()}</Text>
                        {help && (
                          <Text style={{color: isHelpOpen ? T.combat : T.textMuted + '99', fontSize:9, fontWeight:'700'}}>ⓘ</Text>
                        )}
                      </View>
                    </TouchableOpacity>
                    <Text style={{flex:1, color: adv === 2 ? T.win : T.textDim, fontSize:12, fontWeight: adv === 2 ? '800' : '600', textAlign:'right'}}>{fmt(v2)}</Text>
                  </View>
                  {isHelpOpen && help && (
                    <View style={{paddingVertical:6, paddingHorizontal:10, marginBottom:4,
                                  backgroundColor: T.combat + '12', borderRadius:6,
                                  borderLeftWidth:2, borderLeftColor: T.combat}}>
                      <Text style={{color: T.textDim, fontSize:11, lineHeight:15}}>{help}</Text>
                    </View>
                  )}
                </View>
              );
            })}
          </View>
        )}

        {/* Method distribution */}
        {hasMethod && (
          <View style={{marginBottom:14, backgroundColor: T.surface, borderRadius:10, padding:12}}>
            <Text style={{color: T.combat, fontSize:11, fontWeight:'800', letterSpacing:0.6, marginBottom:8}}>METHOD DISTRIBUTION</Text>
            {[
              {label:'KO/TKO', pct:kos, color:'#ff6b6b'},
              {label:'Decision', pct:decs, color:'#5ea9e6'},
              {label:'Submission', pct:subs, color:'#a78bfa'},
            ].filter(m => m.pct != null).map((m, mi) => (
              <View key={mi} style={{marginBottom:6}}>
                <View style={{flexDirection:'row', justifyContent:'space-between', marginBottom:3}}>
                  <Text style={{color: T.textDim, fontSize:11, fontWeight:'700'}}>{m.label}</Text>
                  <Text style={{color: m.color, fontSize:11, fontWeight:'800'}}>{m.pct}%</Text>
                </View>
                <View style={{height:6, backgroundColor: T.border + '44', borderRadius:3, overflow:'hidden'}}>
                  <View style={{height:'100%', width: `${Math.min(100, m.pct!)}%`, backgroundColor: m.color, borderRadius:3}} />
                </View>
              </View>
            ))}
          </View>
        )}

        {/* Round distribution */}
        {hasRounds && (
          <View style={{marginBottom:14, backgroundColor: T.surface, borderRadius:10, padding:12}}>
            <Text style={{color: T.combat, fontSize:11, fontWeight:'800', letterSpacing:0.6, marginBottom:8}}>LIKELY ROUND ENDED</Text>
            <View style={{flexDirection:'row', gap:8, alignItems:'flex-end'}}>
              {rounds.map((r, ri) => {
                const pct = Math.round(r.p * 100);
                const isTop = topRound != null && r.r === topRound.r;
                return (
                  <View key={ri} style={{flex:1, alignItems:'center'}}>
                    <Text style={{color: isTop ? T.combat : T.textMuted, fontSize:10, fontWeight:'800', marginBottom:4}}>{pct}%</Text>
                    <View style={{width:'100%', height:48, backgroundColor: T.border + '30', borderRadius:5, overflow:'hidden', justifyContent:'flex-end'}}>
                      <View style={{width:'100%', height: `${Math.max(4, Math.min(100, pct * 2))}%`, backgroundColor: isTop ? T.combat : T.textDim + '88'}} />
                    </View>
                    <Text style={{color: T.textMuted, fontSize:10, fontWeight:'700', marginTop:4}}>R{r.r}</Text>
                  </View>
                );
              })}
            </View>
          </View>
        )}

        {/* Jerry full read */}
        {hasJerry && (
          <View style={{marginBottom:14, padding:12, backgroundColor: T.hrb + '0F', borderRadius:10, borderLeftWidth:3, borderLeftColor: T.hrb}}>
            <View style={{flexDirection:'row', alignItems:'center', gap:6, marginBottom:6}}>
              <Text style={{color: T.hrb, fontSize:11, fontWeight:'800', letterSpacing:0.5}}>🎤 JERRY'S READ</Text>
              {jerry?.call_verdict && (
                <Text style={{color: jerry.call_verdict === 'BACK' ? T.win : jerry.call_verdict === 'FADE' ? T.loss : T.textDim,
                              fontSize:10, fontWeight:'700'}}>
                  {jerry.call_verdict}{jerry.conviction != null && ` · ${jerry.conviction}`}
                </Text>
              )}
            </View>
            <Text style={{color: T.text, fontSize:13, lineHeight:18}}>{jerryFull}</Text>
          </View>
        )}

        {/* Externals */}
        {hasExternals && (
          <View style={{marginBottom:14, backgroundColor: T.surface, borderRadius:10, padding:12}}>
            <Text style={{color: T.combat, fontSize:11, fontWeight:'800', letterSpacing:0.6, marginBottom:8}}>
              EXTERNAL PICKS ({fightExternals.length})
            </Text>
            {fightExternals.slice(0, 10).map((ep: any, ei: number) => (
              <View key={ei} style={{flexDirection:'row', justifyContent:'space-between', alignItems:'flex-start',
                                     paddingVertical:6,
                                     borderBottomWidth: ei < Math.min(9, fightExternals.length - 1) ? 1 : 0,
                                     borderBottomColor: T.border + '22'}}>
                <View style={{flex:1}}>
                  <Text style={{color: T.text, fontSize:12, fontWeight:'700'}} numberOfLines={1}>
                    {ep.source || 'source'} · {ep.side || ep.pick_side || ep.pick_line || 'pick'}
                  </Text>
                  {ep.rationale && (
                    <Text style={{color: T.textMuted, fontSize:11, marginTop:3, lineHeight:15}} numberOfLines={3}>{ep.rationale}</Text>
                  )}
                </View>
                {ep.odds_american != null && (
                  <Text style={{color: T.textDim, fontSize:11, fontWeight:'700', marginLeft:10}}>
                    {ep.odds_american > 0 ? `+${ep.odds_american}` : ep.odds_american}
                  </Text>
                )}
              </View>
            ))}
          </View>
        )}
      </ScrollView>
    </View>
  );
}
