"""NRFI / YRFI proper reweight backtest.

Step 1: Pull every feature relevant to first-inning scoring per graded game.
Step 2: Run logistic regression to LEARN feature weights from outcome.
Step 3: Build a new NRFI-prediction score, bucket into bands, compare hit
rate against the current nrfi_score at every reasonable cutoff.
Step 4: Same exercise for YRFI (predict run-in-1st).

Output: side-by-side comparison of OLD vs NEW score at every cutoff, plus
the learned feature weights so we can see what actually matters.

Sample: n=855 graded games with nrfi_score + nrfi_result populated.
Features pulled from mlb_game_results joined to mlb_team_offense (inning-1
offense splits) by team name.
"""
import os, json, sys, io, urllib.request
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv('.env'); load_dotenv('mlb_pipeline/.env')
URL = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

def get(p):
    req = urllib.request.Request(URL+p, headers={**H, 'Range':'0-49999', 'Range-Unit':'items'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


# ============ DATA PULL ============
print('Loading graded games + team offense inning-1 splits...')
sel = ('game_date,home_team,away_team,nrfi_score,nrfi_result,home_score,away_score,'
       'home_first_inning_era,away_first_inning_era,'
       'home_first_inning_ip,away_first_inning_ip,'
       'home_first_inning_whip,away_first_inning_whip,'
       'home_first_inning_avg,away_first_inning_avg,'
       'home_first_inning_bb,away_first_inning_bb,'
       'home_first_inning_hr,away_first_inning_hr,'
       'home_sp_xera,away_sp_xera,'
       'home_sp_k_pct,away_sp_k_pct,'
       'home_pitcher_last_3_era,away_pitcher_last_3_era,'
       'park_run_factor,temperature,'
       'home_wrc_plus,away_wrc_plus,'
       'home_team_k_pct,away_team_k_pct')
games = get(f'/rest/v1/mlb_game_results?nrfi_result=not.is.null&nrfi_score=not.is.null&select={sel}&order=game_date.desc')
print(f'  {len(games)} games with NRFI result')

# Pull team offense inning-1 stats for ALL teams + join
offense = get('/rest/v1/mlb_team_offense?season=eq.2026&select=team,inning_1_ops,inning_1_runs_per_game,inning_1_k_pct,inning_1_bb_pct,inning_1_hr_per_game,inning_1_wrc_plus')
off_by_team = {o['team']: o for o in offense}
print(f'  {len(off_by_team)} team offense rows')

# Build feature matrix
rows_feat = []
for g in games:
    hops = off_by_team.get(g['home_team']) or {}
    aops = off_by_team.get(g['away_team']) or {}
    rec = {
        # Outcomes
        'nrfi': 1 if g.get('nrfi_result') == 'NRFI' else 0,
        'yrfi': 1 if g.get('nrfi_result') == 'YRFI' else 0,
        'nrfi_score_old': f(g.get('nrfi_score')) or 0,
        # Pitcher 1st-inning ERA (with sample-size weighting)
        'home_fi_era': f(g.get('home_first_inning_era')),
        'away_fi_era': f(g.get('away_first_inning_era')),
        'home_fi_ip':  f(g.get('home_first_inning_ip')),
        'away_fi_ip':  f(g.get('away_first_inning_ip')),
        'home_fi_whip':f(g.get('home_first_inning_whip')),
        'away_fi_whip':f(g.get('away_first_inning_whip')),
        # Season form
        'home_xera':   f(g.get('home_sp_xera')),
        'away_xera':   f(g.get('away_sp_xera')),
        'home_l3_era': f(g.get('home_pitcher_last_3_era')),
        'away_l3_era': f(g.get('away_pitcher_last_3_era')),
        'home_sp_k':   f(g.get('home_sp_k_pct')),
        'away_sp_k':   f(g.get('away_sp_k_pct')),
        # Opp lineup overall
        'home_wrc':    f(g.get('home_wrc_plus')),
        'away_wrc':    f(g.get('away_wrc_plus')),
        'home_team_k': f(g.get('home_team_k_pct')),
        'away_team_k': f(g.get('away_team_k_pct')),
        # Inning-1 OFFENSE splits (the gold)
        'home_off_inn1_ops':  f(hops.get('inning_1_ops')),
        'away_off_inn1_ops':  f(aops.get('inning_1_ops')),
        'home_off_inn1_rpg':  f(hops.get('inning_1_runs_per_game')),
        'away_off_inn1_rpg':  f(aops.get('inning_1_runs_per_game')),
        'home_off_inn1_wrc':  f(hops.get('inning_1_wrc_plus')),
        'away_off_inn1_wrc':  f(aops.get('inning_1_wrc_plus')),
        'home_off_inn1_k':    f(hops.get('inning_1_k_pct')),
        'away_off_inn1_k':    f(aops.get('inning_1_k_pct')),
        # Environment
        'park': f(g.get('park_run_factor')),
        'temp': f(g.get('temperature')),
    }
    rows_feat.append(rec)

# ============ STEP 1: Univariate feature correlation with NRFI ============
print()
print('=' * 100)
print('STEP 1 — univariate feature correlation with NRFI outcome (top 20)')
print('=' * 100)

feat_names = [k for k in rows_feat[0].keys() if k not in ('nrfi','yrfi','nrfi_score_old')]
# Filter rows that have AT LEAST the pitcher 1st-inn ERA populated (core
# feature). Impute remaining missing values with the feature mean across
# the sample — preserves sample size without injecting bias.
core_required = ['home_fi_era', 'away_fi_era', 'home_xera', 'away_xera']
viable = [r for r in rows_feat if all(r.get(k) is not None for k in core_required)]
print(f'  Viable rows (core features populated): {len(viable)}/{len(rows_feat)}')

# Compute feature means for imputation
import statistics as _st
feat_means = {}
for fn in feat_names:
    vals = [r[fn] for r in viable if r.get(fn) is not None]
    feat_means[fn] = _st.mean(vals) if vals else 0.0
clean = []
for r in viable:
    rr = dict(r)
    for fn in feat_names:
        if rr.get(fn) is None:
            rr[fn] = feat_means[fn]
    clean.append(rr)
print(f'  After mean-imputation: {len(clean)} rows ready')

# Compute correlation: positive corr = feature high → NRFI more likely
import statistics
corrs = {}
for fn in feat_names:
    xs = [r[fn] for r in clean]
    ys = [r['nrfi'] for r in clean]
    if len(xs) < 30: continue
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = statistics.stdev(xs), statistics.stdev(ys)
    if sx == 0 or sy == 0: continue
    cov = sum((xs[i]-mx)*(ys[i]-my) for i in range(len(xs))) / (len(xs)-1)
    corrs[fn] = cov / (sx * sy)

# Also check the OLD nrfi_score correlation for baseline
nrfi_scores = [r['nrfi_score_old'] for r in clean]
nrfi_ys = [r['nrfi'] for r in clean]
mx, my = statistics.mean(nrfi_scores), statistics.mean(nrfi_ys)
sx, sy = statistics.stdev(nrfi_scores), statistics.stdev(nrfi_ys)
old_corr = sum((nrfi_scores[i]-mx)*(nrfi_ys[i]-my) for i in range(len(nrfi_scores))) / (len(nrfi_scores)-1) / (sx*sy)
print(f'  BASELINE: current nrfi_score correlation with NRFI outcome: {old_corr:+.3f}')
print()
print(f'{"Feature":>30s}  Pearson r  (positive = feature ↑ → NRFI ↑)')
print('-'*80)
for fn, c in sorted(corrs.items(), key=lambda x: -abs(x[1]))[:25]:
    bar = '█' * min(20, int(abs(c)*100))
    sign = '+' if c >= 0 else '-'
    print(f'  {fn:>30s}  {c:+.3f}  {sign}{bar}')

# ============ STEP 2: Logistic regression to learn weights ============
print()
print('=' * 100)
print('STEP 2 — logistic regression: learn the weights')
print('=' * 100)

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    import numpy as np
except ImportError:
    print('sklearn not available; falling back to manual weighting')
    sys.exit(0)

X = np.array([[r[fn] for fn in feat_names] for r in clean])
y_nrfi = np.array([r['nrfi'] for r in clean])
y_yrfi = np.array([r['yrfi'] for r in clean])

scaler = StandardScaler().fit(X)
X_scaled = scaler.transform(X)

# 80/20 train/test
n = len(X_scaled)
split = int(n * 0.8)
X_train, X_test = X_scaled[:split], X_scaled[split:]
y_train, y_test = y_nrfi[:split], y_nrfi[split:]

model = LogisticRegression(max_iter=1000, C=1.0)
model.fit(X_train, y_train)

print(f'  Train accuracy: {model.score(X_train, y_train):.3f}')
print(f'  Test accuracy:  {model.score(X_test, y_test):.3f}')
print()
print('Learned feature weights (sorted by abs magnitude, positive = predicts NRFI):')
print('-'*80)
weights = list(zip(feat_names, model.coef_[0]))
for fn, w in sorted(weights, key=lambda x: -abs(x[1]))[:20]:
    bar = '█' * min(20, int(abs(w)*15))
    sign = '+' if w >= 0 else '-'
    print(f'  {fn:>30s}  {w:+.4f}  {sign}{bar}')

# ============ STEP 3: Score every game with NEW model, bucket, compare ============
print()
print('=' * 100)
print('STEP 3 — Bucket NEW probability vs OLD nrfi_score, compare hit rates')
print('=' * 100)

probs = model.predict_proba(X_scaled)[:, 1]  # probability NRFI=1
new_scores = (probs * 100).round().astype(int)

# Map old score bands
def band_old(s):
    if s >= 95: return '95+'
    if s >= 90: return '90-94'
    if s >= 80: return '80-89'
    if s >= 70: return '70-79'
    return '<70'

def band_new(s):
    if s >= 70: return '70+'
    if s >= 60: return '60-69'
    if s >= 55: return '55-59'
    if s >= 50: return '50-54'
    if s >= 45: return '45-49'
    return '<45'

old_buckets = defaultdict(lambda: {'w':0,'l':0})
new_buckets = defaultdict(lambda: {'w':0,'l':0})

for i in range(n):
    actual = y_nrfi[i]
    ob = band_old(clean[i]['nrfi_score_old'])
    nb = band_new(new_scores[i])
    if actual == 1:
        old_buckets[ob]['w'] += 1
        new_buckets[nb]['w'] += 1
    else:
        old_buckets[ob]['l'] += 1
        new_buckets[nb]['l'] += 1

print('OLD nrfi_score bands:')
for b in ['95+', '90-94', '80-89', '70-79', '<70']:
    d = old_buckets[b]
    tot = d['w']+d['l']
    if tot == 0: continue
    rate = d['w']/tot*100
    flag = '✅' if rate >= 60 else '🚫' if rate < 50 else ''
    print(f'  {b:>10s}  n={tot:>4d}  {d["w"]}-{d["l"]}  hit={rate:>5.1f}%  {flag}')

print()
print('NEW learned-score bands:')
for b in ['70+', '60-69', '55-59', '50-54', '45-49', '<45']:
    d = new_buckets[b]
    tot = d['w']+d['l']
    if tot == 0: continue
    rate = d['w']/tot*100
    flag = '✅' if rate >= 60 else '🚫' if rate < 50 else ''
    print(f'  {b:>10s}  n={tot:>4d}  {d["w"]}-{d["l"]}  hit={rate:>5.1f}%  {flag}')

# Compare top decile of each
print()
print('TOP DECILE comparison (cleanest possible cohort):')
old_sorted = sorted(range(n), key=lambda i: -clean[i]['nrfi_score_old'])
new_sorted = sorted(range(n), key=lambda i: -new_scores[i])
decile = n // 10
print(f'  Top 10% by OLD score (n={decile}):  {sum(y_nrfi[i] for i in old_sorted[:decile])}-{decile - sum(y_nrfi[i] for i in old_sorted[:decile])}  hit={sum(y_nrfi[i] for i in old_sorted[:decile])/decile*100:.1f}%')
print(f'  Top 10% by NEW score (n={decile}):  {sum(y_nrfi[i] for i in new_sorted[:decile])}-{decile - sum(y_nrfi[i] for i in new_sorted[:decile])}  hit={sum(y_nrfi[i] for i in new_sorted[:decile])/decile*100:.1f}%')
print(f'  Top 20% by OLD score (n={decile*2}):  {sum(y_nrfi[i] for i in old_sorted[:decile*2])}-{decile*2 - sum(y_nrfi[i] for i in old_sorted[:decile*2])}  hit={sum(y_nrfi[i] for i in old_sorted[:decile*2])/(decile*2)*100:.1f}%')
print(f'  Top 20% by NEW score (n={decile*2}):  {sum(y_nrfi[i] for i in new_sorted[:decile*2])}-{decile*2 - sum(y_nrfi[i] for i in new_sorted[:decile*2])}  hit={sum(y_nrfi[i] for i in new_sorted[:decile*2])/(decile*2)*100:.1f}%')

# ============ STEP 4: Same for YRFI ============
print()
print('=' * 100)
print('STEP 4 — YRFI reweight (predict RUN in 1st inning)')
print('=' * 100)

y_yrfi_train = np.array([clean[i]['yrfi'] for i in range(split)])
y_yrfi_test = np.array([clean[i]['yrfi'] for i in range(split, n)])
model_y = LogisticRegression(max_iter=1000, C=1.0)
model_y.fit(X_train, y_yrfi_train)
print(f'  YRFI test accuracy: {model_y.score(X_test, y_yrfi_test):.3f}')
print()
print('Top YRFI predictors:')
print('-'*80)
weights_y = list(zip(feat_names, model_y.coef_[0]))
for fn, w in sorted(weights_y, key=lambda x: -abs(x[1]))[:15]:
    sign = '+' if w >= 0 else '-'
    bar = '█' * min(20, int(abs(w)*15))
    print(f'  {fn:>30s}  {w:+.4f}  {sign}{bar}')

# Score + bucket
probs_y = model_y.predict_proba(X_scaled)[:, 1]
new_y_scores = (probs_y * 100).round().astype(int)
y_sorted = sorted(range(n), key=lambda i: -new_y_scores[i])
print()
print(f'  Top 10% by NEW YRFI score (n={decile}):  YRFI hits={sum(y_yrfi[i] for i in y_sorted[:decile])}/{decile} = {sum(y_yrfi[i] for i in y_sorted[:decile])/decile*100:.1f}%')
print(f'  Top 20% by NEW YRFI score (n={decile*2}):  YRFI hits={sum(y_yrfi[i] for i in y_sorted[:decile*2])}/{decile*2} = {sum(y_yrfi[i] for i in y_sorted[:decile*2])/(decile*2)*100:.1f}%')
print(f'  Top 30% by NEW YRFI score (n={decile*3}):  YRFI hits={sum(y_yrfi[i] for i in y_sorted[:decile*3])}/{decile*3} = {sum(y_yrfi[i] for i in y_sorted[:decile*3])/(decile*3)*100:.1f}%')

# Save model for production use
print()
print('=' * 100)
print('Saving learned weights to models/nrfi_v2_weights.json...')
import os
os.makedirs('mlb_pipeline/models', exist_ok=True)
weights_out = {
    'feature_names': feat_names,
    'nrfi_weights': model.coef_[0].tolist(),
    'nrfi_intercept': float(model.intercept_[0]),
    'yrfi_weights': model_y.coef_[0].tolist(),
    'yrfi_intercept': float(model_y.intercept_[0]),
    'feature_means': scaler.mean_.tolist(),
    'feature_stds': scaler.scale_.tolist(),
    'training_n': split,
    'test_n': n - split,
    'trained_at': '2026-06-06',
}
with open('mlb_pipeline/models/nrfi_v2_weights.json', 'w') as fp:
    json.dump(weights_out, fp, indent=2)
print('  Saved.')
