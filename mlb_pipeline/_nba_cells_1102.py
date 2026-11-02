"""Does ANY NBA game-level cell clear breakeven in our own graded record?
n>=30, ROI at each row's own price, positive in BOTH date halves."""
import os, sys, requests, collections, statistics
from pathlib import Path
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
for _l in Path('.env').read_text().split('\n'):
    if '=' in _l and not _l.startswith('#'):
        k,v=_l.split('=',1); os.environ.setdefault(k.strip(),v.strip())
SB=os.environ['SUPABASE_URL'].rstrip('/'); K=os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H={'apikey':K,'Authorization':'Bearer '+K}
def page(t,p):
    out,off=[],0
    while off<80000:
        r=requests.get(f'{SB}/rest/v1/{t}',headers=H,params=dict(p,limit=1000,offset=off),timeout=180)
        if r.status_code!=200: print(f'  !! {t} {r.status_code} {r.text[:200]}'); return out
        b=r.json(); out+=b
        if len(b)<1000: return out
        off+=1000
    return out
def f(v):
    try: return float(v)
    except (TypeError,ValueError): return None
rows=page('public_receipts',{'select':'game_date,surface,market,pick_side,pick_line,pick_odds,'
                                     'result,conviction,tier,matchup','sport':'eq.NBA'})
print(f'NBA receipts, all time: {len(rows)}')
g=[r for r in rows if str(r.get('result') or '').upper() in ('WIN','LOSS')]
print(f'  graded WIN/LOSS: {len(g)}')
pr=[r for r in g if f(r.get('pick_odds')) is not None]
print(f'  ...with a real price: {len(pr)}')
print(f'  date range: {min(r["game_date"] for r in pr) if pr else "-"} .. '
      f'{max(r["game_date"] for r in pr) if pr else "-"}')

def roi(sub):
    u=0.0
    for r in sub:
        o=f(r['pick_odds']); w=str(r['result']).upper()=='WIN'
        u += (100/(-o) if o<0 else o/100) if w else -1.0
    return 100*u/len(sub)
def be(sub):
    t=0.0
    for r in sub:
        o=f(r['pick_odds']); t += (-o)/((-o)+100) if o<0 else 100/(o+100)
    return 100*t/len(sub)
def halves(sub):
    s=sorted(sub,key=lambda r:str(r['game_date']))
    m=len(s)//2
    # do not cut inside a date
    while m<len(s)-1 and s[m]['game_date']==s[m-1]['game_date']: m+=1
    return s[:m],s[m:]
def report(label, sub, min_n=30):
    if len(sub)<min_n:
        print(f'  {label:42s} n={len(sub):4d}  (below n>={min_n}, not quoted)')
        return
    w=sum(1 for r in sub if str(r['result']).upper()=='WIN')
    e,b=roi(sub),be(sub)
    a,c=halves(sub)
    ea=roi(a) if len(a)>=8 else None; ec=roi(c) if len(c)>=8 else None
    flag=''
    if e>0 and ea is not None and ec is not None and ea>0 and ec>0: flag='  <-- CLEARS'
    print(f'  {label:42s} n={len(sub):4d}  {100*w/len(sub):5.1f}% vs BE {b:5.1f}%  '
          f'ROI {e:+6.2f}%  halves {("%+.1f"%ea) if ea is not None else "n/a":>6s}/'
          f'{("%+.1f"%ec) if ec is not None else "n/a":>6s}{flag}')

print('\n=== by market ===')
for mk in sorted({str(r['market']) for r in pr}):
    report(f'market={mk}', [r for r in pr if str(r['market'])==mk])
print('\n=== by market x side ===')
for mk in sorted({str(r['market']) for r in pr}):
    for sd in sorted({str(r.get('pick_side')) for r in pr if str(r['market'])==mk}):
        report(f'{mk} / side={sd}', [r for r in pr if str(r['market'])==mk
                                     and str(r.get('pick_side'))==sd])
print('\n=== ml by price band (favourite vs dog) ===')
ml=[r for r in pr if str(r['market'])=='ml']
report('ml  price <= -200 (heavy fav)', [r for r in ml if f(r['pick_odds'])<=-200])
report('ml  -200..-130', [r for r in ml if -200<f(r['pick_odds'])<=-130])
report('ml  -130..+100', [r for r in ml if -130<f(r['pick_odds'])<100])
report('ml  dog +100 or better', [r for r in ml if f(r['pick_odds'])>=100])
print('\n=== by conviction band ===')
cv=[r for r in pr if r.get('conviction') is not None]
for lo,hi in ((0,50),(50,55),(55,60),(60,70),(70,101)):
    report(f'conviction {lo}-{hi}', [r for r in cv if lo<=r['conviction']<hi])
print('\n=== by surface ===')
for sf in sorted({str(r['surface']) for r in pr}):
    report(f'surface={sf}', [r for r in pr if str(r['surface'])==sf])
