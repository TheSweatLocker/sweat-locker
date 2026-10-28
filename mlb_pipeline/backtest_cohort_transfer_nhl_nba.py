#!/usr/bin/env python3
"""Can MLB's cohort signals transfer to NHL and NBA? Measured, not assumed.

== WHY (2026-10-28) =======================================================
R9: enabled signal classes per sport are MLB 22, NCAAF 20, NFL 20, NHL 11,
NBA 11. NHL carries evidence on 90% of its registry, so it is not
calibration-poor, it is VARIETY-poor. `cohort` is the largest family MLB
has that NHL and NBA lack entirely (MLB 15 rows, NHL 0, NBA 0).

The plumbing transfers. The cohort family reads 10 ctx fields; the ones it
leans on hardest are present and fully populated on both sports:

    signal_confluence_net   NHL 297/297    NBA 155/155
    home_ml_close           NHL 212/297    NBA  76/155
    close_spread            present        present

The MLB-only fields (bullpen ERA, NRFI tier, spread_delta) are exactly the
baseball-specific ones and are not needed for the five rules below.

So the question is not "can we wire it" but "does the RULE hold there".
This answers that before a single signal_sources row is inserted, because
`project_ncaaf_conviction_is_scale_error_929` and the retracted NFL prop
edge are both what happens when a rule is transferred on plausibility.

== WHAT IT MEASURES ======================================================
The five cohort rules whose conditions NHL/NBA ctx can actually evaluate,
copied verbatim from the live MLB `signal_sources` rows, graded on the
game's own CLOSING moneyline rather than a flat price - a 74% hit rate on
-350 favourites is not an edge (feedback_flat_110_inflates_every_ml_figure).

Two DIRECTION-ONLY CONTROLS are reported alongside. A cohort rule that
takes HOME has to beat "always take HOME" or it is a home-field preference
wearing a signal's name (feedback_signal_needs_a_direction_control).

== RESULT, 2026-10-28 ====================================================
NHL (n=197 joined, graded and priced):

    rule                          n    hit%    med BE     ROI    split-half
    confluence_home_lean_as_fav  43   74.4%    66.0%   +11.79%  +7.6 -> +15.7
    confluence_home_lean         60   68.3%    64.2%    +7.06%  +10.7 ->  +3.4
    sharp_confluence_alignment   46   63.0%    65.7%    -4.69%
    confluence_away_lean         60   55.0%    55.1%    -5.27%
    CONTROL always HOME         197   56.3%    54.5%    +1.16%

NBA (n=63 joined): every rule negative, both controls negative or mixed.

READ THIS HONESTLY, BECAUSE IT DOES NOT CLEAR THE BAR:
  * The two positive NHL rules DO beat the direction-only control (+11.79%
    and +7.06% against +1.16%) and hold their SIGN split-half. That is the
    encouraging part and it is why these are worth carrying forward.
  * But NEITHER confidence interval clears its own breakeven. For
    `confluence_home_lean_as_fav` the CI is [59.8, 85.1] against a 66.0%
    breakeven, so the lower bound sits BELOW the price. n=43.
  * `confluence_away_lean` and `sharp_confluence_alignment` are NEGATIVE on
    NHL. The family does not transfer as a family. Transferring all five
    because two look good is the exact mistake.
  * NBA is sample-blocked, not signal-blocked: only 63 games have a
    confluence value, a closing ML and a final score together.

CONCLUSION: do NOT enable these at strength. Nothing here justifies a
weighted signal yet. Re-run this script as NHL volume grows; the bar is a
CI that clears breakeven AND split-half holding, the same bar the b2b and
oddscrowd signals were held to today.

    python backtest_cohort_transfer_nhl_nba.py
"""
import collections,json,math,statistics,urllib.parse,urllib.request,urllib.error
import os, sys


def _env(path):
    out = {}
    try:
        for line in open(path, encoding='utf-8', errors='replace'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_E = _env(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
URL = _E.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
KEY = (_E.get('SUPABASE_SERVICE_ROLE_KEY') or _E.get('SUPABASE_KEY')
       or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not URL or not KEY:
    sys.exit('missing SUPABASE_URL / SUPABASE_KEY')
BASE = URL.rstrip('/') + '/rest/v1/'
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}
def raw(table,**p):
    u=BASE+table+'?'+urllib.parse.urlencode(p,safe='.,()*:')
    try:
        with urllib.request.urlopen(urllib.request.Request(u,headers=HDR),timeout=120) as r:
            return json.loads(r.read().decode('utf-8','replace'))
    except urllib.error.HTTPError as e:
        return {'_err':'%s %s'%(e.code,e.read().decode('utf-8','replace')[:160])}
def paged(t,sel,order,**f):
    out,off=[],0
    while True:
        p={'select':sel,'order':order,'limit':'1000','offset':str(off)}; p.update(f)
        r=raw(t,**p)
        if isinstance(r,dict): return None
        out+=r
        if len(r)<1000: return out
        off+=1000
def wil(w,n):
    if not n: return (0.0,0.0)
    p=w/n; z=1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; m=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (100*(c-m),100*(c+m))
def f(v):
    try:
        x=float(v); return x if x==x else None
    except (TypeError,ValueError): return None
def pay(o): return (100.0/(-o)) if o<0 else (o/100.0)
def be(o): return (100.0*(-o)/((-o)+100.0)) if o<0 else (100.0*100.0/(o+100.0))
RULES=[
 ('confluence_home_lean',       lambda n,h,a: 'HOME' if n>=2 else None),
 ('confluence_away_lean',       lambda n,h,a: 'AWAY' if n<=-2 else None),
 ('sharp_confluence_alignment', lambda n,h,a: ('HOME' if n>0 else 'AWAY') if abs(n)>=4 else None),
 ('confluence_home_lean_as_dog',lambda n,h,a: 'HOME' if (n>=2 and h is not None and h>=110) else None),
 ('confluence_home_lean_as_fav',lambda n,h,a: 'HOME' if (n>=2 and h is not None and h<=-150) else None),
 ('CONTROL: always HOME',       lambda n,h,a: 'HOME'),
 ('CONTROL: always AWAY',       lambda n,h,a: 'AWAY'),
]
print('R9: BACKTEST the transferable cohort rules on NHL and NBA ML')
print('    priced at the game\'s own closing ML. Control rows show what the')
print('    rule must BEAT to be a signal rather than a home/away preference.')
for sp,ctb,rtb in (('NHL','nhl_game_context','nhl_game_results'),
                   ('NBA','nba_game_context','nba_game_results')):
    ctx=paged(ctb,'game_id,game_date,signal_confluence_net,home_ml_close,away_ml_close','game_date.asc')
    res=paged(rtb,'game_id,home_score,away_score','game_id.asc')
    if ctx is None or res is None: print('\n  %s: read failed'%sp); continue
    rm={str(x['game_id']):x for x in res}
    joined=[]
    for c in ctx:
        r=rm.get(str(c['game_id']))
        if not r: continue
        hs,as_=f(r.get('home_score')),f(r.get('away_score'))
        n=f(c.get('signal_confluence_net')); h=f(c.get('home_ml_close')); a=f(c.get('away_ml_close'))
        if hs is None or as_ is None or n is None or hs==as_: continue
        if h is None or a is None or -100<h<100 or -100<a<100: continue
        joined.append((str(c['game_date'])[:10],int(n),h,a,'HOME' if hs>as_ else 'AWAY'))
    joined.sort()
    print()
    print('  %s: ctx rows %d  joined+graded+priced %d'%(sp,len(ctx),len(joined)))
    print('    %-30s %5s %7s %-18s %8s %9s %s'%('rule','n','hit%','95% CI','med BE','ROI','split-half ROI'))
    for name,fn in RULES:
        sel=[]
        for d,n,h,a,win in joined:
            s=fn(n,h,a)
            if s is None: continue
            px=h if s=='HOME' else a
            sel.append((d,s==win,px))
        if len(sel)<20:
            print('    %-30s %5d  (n<20, not reported)'%(name,len(sel))); continue
        w=sum(1 for _d,ok,_p in sel if ok); N=len(sel)
        u=sum((pay(p) if ok else -1.0) for _d,ok,p in sel)
        lo,hi=wil(w,N); mbe=be(statistics.median([p for _d,_o,p in sel]))
        half=N//2
        r1=sum((pay(p) if ok else -1.0) for _d,ok,p in sel[:half])/max(half,1)*100
        r2=sum((pay(p) if ok else -1.0) for _d,ok,p in sel[half:])/max(N-half,1)*100
        print('    %-30s %5d %6.1f%% [%5.1f,%5.1f] %7.1f%% %+8.2f%%  %+6.1f%% -> %+6.1f%%'
              %(name,N,100.0*w/N,lo,hi,mbe,100.0*u/N,r1,r2))
