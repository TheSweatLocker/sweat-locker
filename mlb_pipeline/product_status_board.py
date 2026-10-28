#!/usr/bin/env python3
"""Product status: every surface x sport judged against ITS OWN breakeven.

== WHY THIS EXISTS (2026-10-28) ============================================
Andy: "where do we stand acorss all engines for evey sports? ... i just feel
like there are aot of fires to put out on big things and i am not sure
current status of our product".

Two rules make this honest, and both were learned the hard way today:

1. HIT RATE AGAINST A PER-MARKET BREAKEVEN, NOT UNITS. Most wins are
   unpriced -- game_read MLB is 19% priced, sweat_card MLB 35%, potd 32%,
   dawg 0% -- so a unit total for those cells cannot be computed at all. A
   hit rate needs no price. The breakeven is weighted by the cell's own
   market mix, from MEDIAN payout per market (ml 58.33%, prop 52.83%,
   rl/total 52.38%); a flat -110 would call a 57% moneyline surface a winner
   when it is losing.

2. A LOSS IS -1 WHATEVER THE PRICE; A WIN WITH NO PRICE IS UNKNOWABLE. My
   first version of this board summed only priced rows, so 42 unpriced degen
   LOSSES contributed 0 while 23 priced wins contributed full payout -- and
   it printed daily_degen at +45.03u. The real figure is not computable. That
   is the exact inverse of the trap compute_external_source_records already
   documents ("every loss counted, every win worth nothing"). Units are now
   WITHHELD for any cell with an unpriced win rather than silently skewed.

VERDICTS
  CLEARS    the CI lower bound is above breakeven -- a real edge
  straddles cannot be distinguished from breakeven at this n. NOT "bad".
  BELOW     the CI upper bound is under breakeven -- a measured loser

    python product_status_board.py
"""
import collections,math,json,os,sys,urllib.error,urllib.parse,urllib.request
exec(open('_aud_1028.py').read().split("D='2026-10-27'")[0])
def wil(w,n):
    if n==0: return (0.0,0.0)
    p=w/n; z=1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; m=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (100*(c-m),100*(c+m))
# measured breakevens from MEDIAN payout per market (mean is skewed by longshots)
BE={'ml':58.33,'prop':52.83,'rl':52.38,'total':52.38,'potd':52.40,'fight':52.40,
    'parlay':12.70,'game':52.38}
def be_for(mks):
    # weight each market's breakeven by its share of the cell
    tot=sum(mks.values()); 
    if not tot: return 52.38
    s=0.0
    for m,n in mks.items():
        key='rl' if m in ('rl','pl','puckline','spread') else ('total' if m in ('total','ou') else m)
        s+=BE.get(key,52.38)*n
    return s/tot
print('='*112)
print('PRODUCT STATUS: every surface x sport, hit rate against ITS OWN breakeven')
print('  units are not the metric here - most wins are unpriced, so hit vs breakeven is what can be judged')
print('='*112)
r=[x for x in get('public_receipts', select='sport,surface,market,result,pick_odds')
   if str(x.get('result') or '').upper() in ('WIN','LOSS')]
by=collections.defaultdict(list)
for x in r: by[(str(x['surface']),str(x['sport']))].append(x)
rows=[]
for k,v in by.items():
    if len(v)<15: continue
    w=sum(1 for x in v if str(x['result']).upper()=='WIN')
    n=len(v); lo,hi=wil(w,n)
    mks=collections.Counter(str(x.get('market') or 'game').lower() for x in v)
    be=be_for(mks)
    hit=100.0*w/n
    priced=sum(1 for x in v if x.get('pick_odds') is not None)
    if lo>be: vd='CLEARS'
    elif hi<be: vd='BELOW'
    else: vd='straddles'
    rows.append((k,n,w,hit,lo,hi,be,vd,100.0*priced/n,dict(mks.most_common(2))))
print('  %-12s %-6s %6s %7s %-15s %7s %-10s %6s'%('surface','sport','n','hit%','95% CI','breakev','verdict','priced'))
print('  '+'-'*98)
for (k,n,w,hit,lo,hi,be,vd,pc,mks) in sorted(rows,key=lambda z:(z[7]!='CLEARS',z[7]!='straddles',-z[1])):
    print('  %-12s %-6s %6d %6.1f%% [%4.1f,%4.1f] %6.1f%% %-10s %5.0f%%'%(
        k[0][:12],k[1],n,hit,lo,hi,be,vd,pc))
print()
print('  CLEARS   = CI lower bound above breakeven, a real edge')
print('  straddles= cannot distinguish from breakeven at this n')
print('  BELOW    = CI upper bound under breakeven, a measured loser')
