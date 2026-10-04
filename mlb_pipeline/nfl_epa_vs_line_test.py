"""Does opponent-adjusted EPA add signal GIVEN the closing line?

Beating actual margin is a stat. Beating the line is a bet. This runs the
joint regression on every graded game where we have both, walk-forward.
"""
import os, collections, statistics
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
import requests
from nfl_opponent_adjusted_epa import _pull, team_week_epa, fit_ratings

U=os.environ['SUPABASE_URL']; K=os.environ['SUPABASE_KEY']
H={'apikey':K,'Authorization':'Bearer '+K}

rows=_pull('nfl_player_stats',{'select':'season,week,team,opponent_team,season_type,passing_epa,rushing_epa'})
tw=team_week_epa(rows)
res=_pull('nfl_game_results',{'select':'season,week,home_team,away_team,home_score,away_score,close_spread'})
games=[g for g in res if g.get('home_score') is not None and g.get('close_spread') is not None]
print('team-weeks %d · graded games with a closing line %d'%(len(tw),len(games)))

by_season=collections.defaultdict(list)
for k,v in tw.items(): by_season[k[0]].append((k[1],k[2],v))

X_mkt=[];X_epa=[];Y=[]
for season,entries in sorted(by_season.items()):
    weeks=sorted({w for w,_,_ in entries})
    for wk in weeks:
        if wk<4: continue
        hist=[(t,c['opp'],c['off']) for w,t,c in entries if w<wk]
        if len(hist)<40: continue
        off,dfn,mu=fit_ratings(hist)
        for g in games:
            if g.get('season')!=season or g.get('week')!=wk: continue
            h,a=g['home_team'],g['away_team']
            if h not in off or a not in off: continue
            # nfl close_spread is the AWAY line -> market home margin = close_spread
            mkt=float(g['close_spread'])
            epa=(off.get(h,0)+dfn.get(a,0))-(off.get(a,0)+dfn.get(h,0))
            X_mkt.append(mkt); X_epa.append(epa); Y.append(float(g['home_score'])-float(g['away_score']))
n=len(Y)
print('walk-forward sample: %d games'%n)
def corr(a,b):
    m1,m2=sum(a)/len(a),sum(b)/len(b)
    s12=sum((x-m1)*(y-m2) for x,y in zip(a,b)); s11=sum((x-m1)**2 for x in a); s22=sum((y-m2)**2 for y in b)
    return s12/((s11*s22)**0.5) if s11 and s22 else 0
m1,m2,my=sum(X_mkt)/n,sum(X_epa)/n,sum(Y)/n
s11=sum((x-m1)**2 for x in X_mkt); s22=sum((x-m2)**2 for x in X_epa)
s12=sum((a-m1)*(b-m2) for a,b in zip(X_mkt,X_epa))
s1y=sum((a-m1)*(y-my) for a,y in zip(X_mkt,Y)); s2y=sum((b-m2)*(y-my) for b,y in zip(X_epa,Y))
det=s11*s22-s12*s12
b1=(s22*s1y-s12*s2y)/det; b2=(s11*s2y-s12*s1y)/det
print()
print('  corr(market, adjEPA)      = %+.3f'%corr(X_mkt,X_epa))
print('  margin ~ %.2f*market + %.3f*adjEPA'%(b1,b2))
print('  -> %s'%('adjEPA ADDS signal over the line' if abs(b2)>0.15 else 'adjEPA adds ~nothing over the line'))
print()
# ATS test: does siding with adjEPA vs the line beat 52.4%?
w=l=0
for mkt,epa,y in zip(X_mkt,X_epa,Y):
    edge=epa-mkt
    if abs(edge)<1: continue
    pick_home=edge>0
    cover_home=(y-mkt)>0
    if abs(y-mkt)<1e-9: continue
    if pick_home==cover_home: w+=1
    else: l+=1
print('  ATS siding with adjEPA vs the line: %d-%d (%.1f%%) n=%d'%(w,l,100*w/max(1,w+l),w+l))
