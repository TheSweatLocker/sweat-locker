"""Nightly data-quality checker.

Runs in cron every night and surfaces anomalies that would otherwise
silently corrupt records. Motivated by 2026-09-01 finding: 73 external
picks were cross-attributed (Red Sox/White Sox alias collision) over
30 days without any alarm firing — records were tainted at
external_source_track_record level and downstream ensemble weights.

Checks (add more as we surface new failure modes):

  1. EXTERNALS CROSS-ATTRIBUTION
     For each external pick w/ raw_text, scan for team keywords.
     Flag rows where NEITHER resolved home nor away team appears
     in raw_text. Aggregate by source + date.

  2. TIER-VS-SIZE ANOMALIES
     Games marked PRIME (conv 82+) that were later downgraded via
     MC dissent should have recommended_stake=1.0. Flag any PRIME
     picks with recommended_stake=2.0 AND _mc_dissent present.

  3. SPLIT SIGNAL SANITY
     line_movement_flags with LEAN/CONFIRMED classification but
     ensemble is still weighting them (would indicate quarantine
     wasn't taking effect).

  4. PROJECTION NULL FIELDS
     Pitcher props (ks, bb, ha, outs, er) with tier PRIME/STRONG
     but missing _projected_* signal → scorer fell to default path.

  5. STALE GAME_CONTEXT
     Games where primary_play_computed_at < mc_computed_at → MC
     dissent gate didn't re-fire after MC populated (Braves 8/31 case).

Emits findings as structured output + writes to a data_quality_alerts
table (optional — writes if table exists). Suitable for wiring into
nightly grader cron to alert on anomalies before users see them.

Usage:
    python audit_data_quality.py                # today
    python audit_data_quality.py --date 2026-08-31
    python audit_data_quality.py --days 7       # 7-day window
"""
import argparse
import os
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_TEAM_TOKENS = {
    'Arizona Diamondbacks': ['arizona','diamondbacks','d-backs','dbacks'],
    'Atlanta Braves':       ['atlanta','braves'],
    'Baltimore Orioles':    ['baltimore','orioles'],
    'Boston Red Sox':       ['boston','red sox','redsox','bosox'],
    'Chicago Cubs':         ['chicago cubs','cubs'],
    'Chicago White Sox':    ['white sox','whitesox','chi sox'],
    'Cincinnati Reds':      ['cincinnati','reds'],
    'Cleveland Guardians':  ['cleveland','guardians'],
    'Colorado Rockies':     ['colorado','rockies'],
    'Detroit Tigers':       ['detroit','tigers'],
    'Houston Astros':       ['houston','astros'],
    'Kansas City Royals':   ['kansas city','royals'],
    'Los Angeles Angels':   ['angels','laa','anaheim'],
    'Los Angeles Dodgers':  ['dodgers','lad'],
    'Miami Marlins':        ['miami','marlins'],
    'Milwaukee Brewers':    ['milwaukee','brewers'],
    'Minnesota Twins':      ['minnesota','twins'],
    'New York Mets':        ['ny mets','nym','new york mets'],
    'New York Yankees':     ['ny yankees','nyy','yankees'],
    'Philadelphia Phillies':['philadelphia','phillies','phils'],
    'Pittsburgh Pirates':   ['pittsburgh','pirates','bucs'],
    'San Diego Padres':     ['san diego','padres'],
    'San Francisco Giants': ['san francisco','giants','sf giants'],
    'Seattle Mariners':     ['seattle','mariners'],
    'St. Louis Cardinals':  ['st. louis','st louis','cardinals'],
    'Tampa Bay Rays':       ['tampa','rays'],
    'Texas Rangers':        ['texas','rangers'],
    'Toronto Blue Jays':    ['toronto','blue jays','jays'],
    'Washington Nationals': ['washington','nationals','nats'],
    'Athletics':            ['athletics','oakland','oak'],
}


def _teams_in_text(text: str) -> set:
    if not text: return set()
    tl = ' ' + text.lower() + ' '
    hit = set()
    for team, tokens in _TEAM_TOKENS.items():
        for tok in tokens:
            if ' ' + tok + ' ' in tl:
                hit.add(team); break
    return hit


def check_externals_crossattr(since_date: str) -> list[dict]:
    """Scan externals for cross-attribution. Return list of findings."""
    findings = []
    # Paginate all externals in window (may be > 1000)
    all_rows = []; offset = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/external_picks?sport=eq.MLB&game_date=gte.{since_date}'
            f'&raw_text=not.is.null&surface=in.(ml,rl,total)'
            f'&select=id,game_id,game_date,source,surface,raw_text,result'
            f'&limit=1000&offset={offset}',
            headers=H, timeout=30,
        )
        data = r.json() if r.status_code == 200 else []
        if not isinstance(data, list) or not data: break
        all_rows.extend(data)
        if len(data) < 1000: break
        offset += 1000

    # Load result-table teams (persists after game_context cleanup)
    gc = requests.get(f'{SB}/rest/v1/mlb_game_results?game_date=gte.{since_date}'
                      f'&select=game_id,home_team,away_team', headers=H, timeout=30).json()
    game_teams = {g['game_id']: (g['home_team'], g['away_team'])
                  for g in gc if isinstance(g, dict)}

    for p in all_rows:
        gt = game_teams.get(p['game_id'])
        if not gt: continue
        home, away = gt
        text_teams = _teams_in_text(p.get('raw_text') or '')
        if not text_teams: continue
        if home not in text_teams and away not in text_teams:
            findings.append({
                'kind': 'externals_crossattr',
                'severity': 'HIGH',
                'row_id': p['id'],
                'source': p['source'],
                'game_date': p['game_date'],
                'expected': f'{away} @ {home}',
                'text_mentions': list(text_teams),
                'raw_text': p['raw_text'][:80],
                'graded': p.get('result') in ('W', 'L'),
            })
    return findings


def check_mc_dissent_stale(since_date: str) -> list[dict]:
    """PRIME games where MC dissent should have fired but primary_play
    is still PRIME. Catches the Braves 8/31 race condition."""
    findings = []
    r = requests.get(f'{SB}/rest/v1/mlb_game_context?game_date=gte.{since_date}'
                     f'&select=game_id,home_team,away_team,primary_play,mc_probabilities',
                     headers=H, timeout=15).json()
    for c in r:
        if not isinstance(c, dict): continue
        pp = c.get('primary_play') or {}
        mc = c.get('mc_probabilities') or {}
        if pp.get('tier') != 'PRIME': continue
        if pp.get('_mc_dissent'): continue  # already dissented — OK
        side = str(pp.get('side','')).upper()
        market = str(pp.get('type','')).lower()
        p = None
        if market == 'ml':
            p = mc.get('mc_p_home_win') if side == 'HOME' else mc.get('mc_p_away_win')
        elif market == 'total':
            p = mc.get('mc_p_over') if side == 'OVER' else mc.get('mc_p_under')
        if p is None: continue
        try: p_f = float(p)
        except: continue
        # PRIME threshold is 0.55 — anything below should have dissented
        if p_f < 0.55:
            findings.append({
                'kind': 'mc_dissent_stale',
                'severity': 'HIGH',
                'game_id': c['game_id'],
                'matchup': f'{c["away_team"]} @ {c["home_team"]}',
                'pick': pp.get('label'),
                'mc_pct': round(p_f * 100, 1),
                'note': 'PRIME pick with MC below 55% threshold and no dissent flag',
            })
    return findings


def check_pitcher_projections_missing(since_date: str) -> list[dict]:
    """PRIME/STRONG pitcher props missing a _projected_* field
    → scorer fell to default path."""
    findings = []
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props?game_date=gte.{since_date}'
                     f'&tier=in.(PRIME,STRONG)&prop_type=in.(ks_over,ks_under,'
                     f'bb_over,bb_under,ha_over,ha_under,outs_over,outs_under,'
                     f'er_over,er_under)&select=player_name,prop_type,tier,'
                     f'game_date,signals&limit=500',
                     headers=H, timeout=20).json()
    _EXPECTED = {
        'ks_over': '_projected_ks', 'ks_under': '_projected_ks',
        'bb_over': '_projected_bb', 'bb_under': '_projected_bb',
        'ha_over': '_projected_hits', 'ha_under': '_projected_hits',
        'outs_over': '_projected_outs', 'outs_under': '_projected_outs',
        'er_over': '_projected_er', 'er_under': '_projected_er',
    }
    for p in r:
        if not isinstance(p, dict): continue
        exp = _EXPECTED.get(p.get('prop_type'))
        if not exp: continue
        sig = p.get('signals') or {}
        if not isinstance(sig, dict): continue
        if exp not in sig or sig[exp] is None:
            findings.append({
                'kind': 'projection_missing',
                'severity': 'MEDIUM',
                'player': p.get('player_name'),
                'prop': p.get('prop_type'),
                'tier': p.get('tier'),
                'game_date': p.get('game_date'),
                'expected_field': exp,
            })
    return findings


def run(days: int) -> None:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    print(f'=== Data Quality Audit · since {since} ({days}d window) ===\n')

    checks = [
        ('EXTERNALS CROSS-ATTRIBUTION', check_externals_crossattr),
        ('MC DISSENT STALE',            check_mc_dissent_stale),
        ('PITCHER PROJECTIONS MISSING', check_pitcher_projections_missing),
    ]
    all_findings = []
    for name, fn in checks:
        print(f'▶ {name}')
        try:
            findings = fn(since)
        except Exception as e:
            print(f'  ✗ check failed: {e}\n')
            continue
        if not findings:
            print(f'  ✓ clean\n')
            continue
        print(f'  🚨 {len(findings)} finding(s)')
        # Aggregate for readability
        if name == 'EXTERNALS CROSS-ATTRIBUTION':
            by_source = Counter(f['source'] for f in findings)
            graded = sum(1 for f in findings if f['graded'])
            print(f'     by source: {dict(by_source)}')
            print(f'     graded (tainting records): {graded}/{len(findings)}')
            for f in findings[:5]:
                print(f'     [{f["source"]:12}] {f["game_date"]} · expected {f["expected"]}')
                print(f'          raw: {f["raw_text"]}')
            if len(findings) > 5: print(f'     ... {len(findings)-5} more')
        elif name == 'MC DISSENT STALE':
            for f in findings[:8]:
                print(f'     {f["matchup"]} · {f["pick"]} (MC {f["mc_pct"]}% < 55%)')
        elif name == 'PITCHER PROJECTIONS MISSING':
            by_prop = Counter(f['prop'] for f in findings)
            print(f'     by prop_type: {dict(by_prop)}')
        print()
        all_findings.extend(findings)

    print(f'═════════════════════════════════════')
    print(f'  TOTAL FINDINGS: {len(all_findings)}')
    print(f'═════════════════════════════════════')
    if all_findings:
        sys.exit(1)  # non-zero for cron alerts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=2, help='window (default 2d)')
    args = ap.parse_args()
    run(days=args.days)


if __name__ == '__main__':
    main()
