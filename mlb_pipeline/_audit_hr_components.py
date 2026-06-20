"""Component-level HR Watch audit.

User finding (6/20): score 70+ has 0pt lift over baseline (18.2% vs 18.9%).
Question: which COMPONENTS of the score actually correlate with HR outcomes?

Pulls last 30/60d of resolved HR Watch rows + breaks lift down by each
score input. Anything that doesn't outperform baseline gets cut from the
recalibrated score formula.
"""
import os, requests
from collections import defaultdict
from datetime import date, timedelta
from dotenv import load_dotenv
load_dotenv()
SU = os.environ['SUPABASE_URL']; SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

today = date.today()
since = (today - timedelta(days=60)).isoformat()

r = requests.get(f'{SU}/rest/v1/mlb_hr_watch',
    params={'game_date': f'gte.{since}',
            'resolved_at': 'not.is.null',
            'select': ('player_name,game_date,hr_hit,score,hr_rate,projected_hr_prob,'
                       'fb_score,env_score,contact_score,jerry_hr_contribution,'
                       'park_factor,is_fallback,due_signal'),
            'order': 'game_date.desc',
            'limit': '2000'},
    headers=H, timeout=30)
rows = r.json()
print(f'pulled {len(rows)} resolved HR Watch rows last 60d')
baseline = sum(1 for r in rows if r.get('hr_hit')) / max(1, len(rows))
print(f'baseline HR rate: {baseline*100:.1f}%')
print()


def lift_band(label, predicate):
    band = [r for r in rows if predicate(r)]
    if not band: return f'{label:40s}: n=0'
    hits = sum(1 for r in band if r.get('hr_hit'))
    rate = hits / len(band)
    lift = (rate - baseline) * 100
    flag = '✓' if abs(lift) >= 2 else '·'
    return f'{label:40s}: {hits:>3}/{len(band):<4} {rate*100:>5.1f}% (lift {lift:+.1f}pt) {flag}'


print('SCORE BANDS (current scoring)')
print('-'*70)
for lo, hi, lbl in [(None, 60, '<60'), (60, 70, '60-69'), (70, 80, '70-79'),
                     (80, 90, '80-89'), (90, 100, '90-99'), (100, None, '100+')]:
    def fn(r, lo=lo, hi=hi):
        s = r.get('score')
        if s is None: return False
        if lo is not None and s < lo: return False
        if hi is not None and s >= hi: return False
        return True
    print(' ', lift_band(f'score {lbl}', fn))
print()
print('PROJECTED HR PROB BANDS (Bayesian-regressed)')
print('-'*70)
for lo, hi, lbl in [(None, 0.08, '<8%'), (0.08, 0.10, '8-10%'), (0.10, 0.12, '10-12%'),
                     (0.12, 0.15, '12-15%'), (0.15, None, '15%+')]:
    def fn(r, lo=lo, hi=hi):
        p = r.get('projected_hr_prob')
        if p is None: return False
        try: p = float(p)
        except: return False
        if lo is not None and p < lo: return False
        if hi is not None and p >= hi: return False
        return True
    print(' ', lift_band(f'proj_hr_prob {lbl}', fn))
print()
print('RAW HR_RATE BANDS (unregressed)')
print('-'*70)
for lo, hi, lbl in [(None, 0.03, '<3%'), (0.03, 0.05, '3-5%'), (0.05, 0.07, '5-7%'),
                     (0.07, None, '7%+')]:
    def fn(r, lo=lo, hi=hi):
        p = r.get('hr_rate')
        if p is None: return False
        try: p = float(p)
        except: return False
        if lo is not None and p < lo: return False
        if hi is not None and p >= hi: return False
        return True
    print(' ', lift_band(f'hr_rate {lbl}', fn))
print()
print('PARK FACTOR BANDS')
print('-'*70)
for lo, hi, lbl in [(None, 95, '<95 pitcher'), (95, 100, '95-99'), (100, 105, '100-104'),
                     (105, 110, '105-109'), (110, None, '110+ hitter')]:
    def fn(r, lo=lo, hi=hi):
        p = r.get('park_factor')
        if p is None: return False
        if lo is not None and p < lo: return False
        if hi is not None and p >= hi: return False
        return True
    print(' ', lift_band(f'park {lbl}', fn))
print()
print('ENV SCORE COMPONENT')
print('-'*70)
for lo, hi, lbl in [(None, 0, '<=0'), (0, 10, '0-9'), (10, 20, '10-19'),
                     (20, 30, '20-29'), (30, None, '30+')]:
    def fn(r, lo=lo, hi=hi):
        p = r.get('env_score')
        if p is None: return False
        if lo is not None and p < lo: return False
        if hi is not None and p >= hi: return False
        return True
    print(' ', lift_band(f'env_score {lbl}', fn))
print()
print('CONTACT SCORE COMPONENT')
print('-'*70)
for lo, hi, lbl in [(None, 0, '<=0'), (0, 5, '0-4'), (5, 10, '5-9'),
                     (10, 15, '10-14'), (15, None, '15+')]:
    def fn(r, lo=lo, hi=hi):
        p = r.get('contact_score')
        if p is None: return False
        if lo is not None and p < lo: return False
        if hi is not None and p >= hi: return False
        return True
    print(' ', lift_band(f'contact_score {lbl}', fn))
print()
print('FB SCORE COMPONENT (opp pitcher fly-ball rate)')
print('-'*70)
for lo, hi, lbl in [(None, 0, '<=0'), (0, 4, '1-3'), (4, 8, '4-7'),
                     (8, None, '8+')]:
    def fn(r, lo=lo, hi=hi):
        p = r.get('fb_score')
        if p is None: return False
        if lo is not None and p < lo: return False
        if hi is not None and p >= hi: return False
        return True
    print(' ', lift_band(f'fb_score {lbl}', fn))
print()
print('JERRY HR CONTRIBUTION (allocated HR-equivalent points)')
print('-'*70)
for lo, hi, lbl in [(None, 0.1, '<0.1'), (0.1, 0.2, '0.1-0.2'), (0.2, 0.25, '0.2-0.25'),
                     (0.25, 0.3, '0.25-0.3'), (0.3, None, '0.3+')]:
    def fn(r, lo=lo, hi=hi):
        p = r.get('jerry_hr_contribution')
        if p is None: return False
        try: p = float(p)
        except: return False
        if lo is not None and p < lo: return False
        if hi is not None and p >= hi: return False
        return True
    print(' ', lift_band(f'jerry_hr {lbl}', fn))
print()
print('DUE SIGNAL (cold surface + hot Statcast)')
print('-'*70)
print(' ', lift_band('due_signal True', lambda r: bool(r.get('due_signal'))))
print(' ', lift_band('due_signal False', lambda r: not bool(r.get('due_signal'))))
print()
print('IS_FALLBACK (no Statcast data — lower-quality rows)')
print('-'*70)
print(' ', lift_band('is_fallback True', lambda r: bool(r.get('is_fallback'))))
print(' ', lift_band('is_fallback False', lambda r: not bool(r.get('is_fallback'))))
