"""Backfill audit for the 5/29-5/30 sweat redesign + NRFI demotion.

Pulls last N days of resolved games (mlb_game_context + mlb_game_results),
re-scores each with the new dimensional scorer, grades the headline play
and supplementary play against actual outcomes, and reports per-dimension
hit rates.

Goal: answer "did the redesign actually improve POTD-grade calls, or just
shuffle the deck?" before tomorrow morning's first live cron under the
new code.

Usage: python _audit_sweat_redesign.py [days=14]
"""
import os
import sys
import io
import json
import urllib.request
import urllib.parse
from collections import Counter
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from play_of_day import score_mlb_game  # noqa: E402

URL = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _get(path):
    req = urllib.request.Request(URL + path, headers=H)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _pick_team_from_label(label, away, home):
    """Given a 'Yankees ML' / 'X ML lean' label and the matchup, return
    'home' / 'away' / None."""
    if not label or not away or not home:
        return None
    l = label.lower()
    # Try last names first
    home_last = home.split()[-1].lower()
    away_last = away.split()[-1].lower()
    if home_last in l:
        return 'home'
    if away_last in l:
        return 'away'
    return None


def grade_play(play, ctx, result):
    """Return ('win'/'loss'/'push'/'no_grade', detail). Plays:
    - SIDE ml/spread/rl
    - TOTAL over/under
    - NRFI/YRFI
    """
    if not play:
        return ('no_grade', 'no play')
    ptype = (play.get('type') or '').upper()
    label = play.get('label') or ''
    home_team = ctx.get('home_team')
    away_team = ctx.get('away_team')

    # NRFI / YRFI
    if ptype in ('NRFI', 'YRFI'):
        nr = result.get('nrfi_result')
        if nr is None:
            return ('no_grade', f'no nrfi_result')
        if nr == ptype:
            return ('win', f'nrfi_result={nr}')
        return ('loss', f'nrfi_result={nr}')

    # TOTAL Over/Under
    if 'TOTAL_OVER' in ptype or 'OVER' == ptype:
        tr = result.get('total_result')
        if tr is None:
            return ('no_grade', 'no total_result')
        if tr == 'Over':
            return ('win', f'total_result={tr}')
        if tr == 'push':
            return ('push', f'total_result=push')
        return ('loss', f'total_result={tr}')
    if 'TOTAL_UNDER' in ptype or 'UNDER' == ptype:
        tr = result.get('total_result')
        if tr is None:
            return ('no_grade', 'no total_result')
        if tr == 'Under':
            return ('win', f'total_result={tr}')
        if tr == 'push':
            return ('push', f'total_result=push')
        return ('loss', f'total_result={tr}')

    # SIDE ml
    if ptype == 'ML':
        side = _pick_team_from_label(label, away_team, home_team)
        if side is None:
            return ('no_grade', f'could not match team in label "{label}"')
        try:
            hs = int(result.get('home_score'))
            as_ = int(result.get('away_score'))
        except (TypeError, ValueError):
            return ('no_grade', 'missing scores')
        if hs == as_:
            return ('push', 'tie')
        winner = 'home' if hs > as_ else 'away'
        return ('win' if winner == side else 'loss', f'{home_team} {hs} - {away_team} {as_}')

    # Run line / spread
    if ptype in ('SPREAD', 'RL'):
        side = _pick_team_from_label(label, away_team, home_team)
        if side is None:
            return ('no_grade', f'could not match team in label "{label}"')
        rlr = result.get('run_line_result')
        if rlr is None:
            return ('no_grade', 'no run_line_result')
        if rlr == 'push':
            return ('push', 'push')
        return ('win' if rlr == side else 'loss', f'run_line={rlr}')

    return ('no_grade', f'unknown play type {ptype}')


def main(days=14):
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()
    print(f'Audit window: {start} → {end} ({days} days)')
    print()

    # Pull mlb_game_results — it preserves both the historical scoring
    # inputs (xera / NRFI / confluence / total / spread etc.) AND the
    # actual outcomes. mlb_game_context wipes historical rows after a
    # few days so we can't source from there.
    res_rows = _get(
        f'/rest/v1/mlb_game_results?game_date=gte.{start}&game_date=lt.{end}&select=*'
    )
    by_id = {r.get('game_id'): r for r in res_rows}
    ctx_rows = res_rows  # use result rows as scoring inputs
    print(f'Pulled {len(res_rows)} game_results rows (used as both input + outcome source)')

    # Pull props for all games in window (for prop_dir computation)
    prop_rows = _get(
        f'/rest/v1/mlb_pipeline_props?game_date=gte.{start}&game_date=lt.{end}'
        f'&select=game_id,tier,prop_type,player_name,conviction'
    )
    props_by_game = {}
    for p in prop_rows:
        if p.get('game_id'):
            props_by_game.setdefault(p['game_id'], []).append(p)
    print(f'Pulled {len(prop_rows)} prop rows, grouped into {len(props_by_game)} games')
    print()

    # Re-score every game and grade
    headline_grades = []   # list of (dim, tier, grade, label, matchup)
    supp_grades = []       # supplementary play grades

    for ctx in ctx_rows:
        gid = ctx.get('game_id')
        result = by_id.get(gid)
        if not result:
            continue
        try:
            _, dims = score_mlb_game(ctx, game_props=props_by_game.get(gid, []))
        except Exception as e:
            print(f'  scoring failed for {gid}: {e}')
            continue

        win = dims['winning_dimension']
        play = dims['model_play']
        tier = dims[win]['tier']
        grade, detail = grade_play(play, ctx, result)
        headline_grades.append({
            'date': ctx.get('game_date'),
            'matchup': f"{ctx.get('away_team')} @ {ctx.get('home_team')}",
            'dim': win,
            'tier': tier,
            'play_type': (play or {}).get('type'),
            'play_label': (play or {}).get('label'),
            'grade': grade,
            'detail': detail,
        })

        supp = dims.get('supplementary_play')
        if supp:
            sgrade, sdetail = grade_play(supp, ctx, result)
            supp_grades.append({
                'date': ctx.get('game_date'),
                'matchup': f"{ctx.get('away_team')} @ {ctx.get('home_team')}",
                'tier': supp.get('tier'),
                'play_type': supp.get('type'),
                'grade': sgrade,
                'companion': supp.get('sub', '').lower().count(' + ') > 0,
            })

    # Reports
    def pct(wins, losses, label):
        n = wins + losses
        if n == 0:
            return f'  {label}: 0-0 (no graded sample)'
        rate = wins / n * 100
        return f'  {label}: {wins}-{losses} ({rate:.1f}%, n={n})'

    print('=' * 70)
    print(f'HEADLINE PLAY HIT RATES — {len(headline_grades)} games scored')
    print('=' * 70)

    # By dimension × tier
    for dim in ('side', 'total', 'prop'):
        print(f'\n{dim.upper()} dimension:')
        for tier in ('PRIME', 'STRONG', 'LIGHT_LEAN'):
            wins = sum(1 for g in headline_grades if g['dim'] == dim and g['tier'] == tier and g['grade'] == 'win')
            losses = sum(1 for g in headline_grades if g['dim'] == dim and g['tier'] == tier and g['grade'] == 'loss')
            print(pct(wins, losses, tier))

    # By play type
    print(f'\nBy play type (across all dimensions):')
    types = Counter(g['play_type'] for g in headline_grades if g['play_type'])
    for ptype, _ in types.most_common():
        wins = sum(1 for g in headline_grades if g['play_type'] == ptype and g['grade'] == 'win')
        losses = sum(1 for g in headline_grades if g['play_type'] == ptype and g['grade'] == 'loss')
        no_grade = sum(1 for g in headline_grades if g['play_type'] == ptype and g['grade'] == 'no_grade')
        print(pct(wins, losses, f'{ptype} (no_grade={no_grade})'))

    # Distribution by day
    by_date = {}
    for g in headline_grades:
        by_date.setdefault(g['date'], Counter())[g['tier']] += 1
    print(f'\nTier distribution per slate (last 5 days):')
    for d in sorted(by_date.keys())[-5:]:
        cnt = by_date[d]
        n = sum(cnt.values())
        print(f'  {d}: PRIME={cnt.get("PRIME",0)} STRONG={cnt.get("STRONG",0)} LIGHT={cnt.get("LIGHT_LEAN",0)} PASS={cnt.get("PASS",0)} (total={n})')

    # Supplementary
    print()
    print('=' * 70)
    print(f'SUPPLEMENTARY PLAY HIT RATES — {len(supp_grades)} games with supp tag')
    print('=' * 70)
    for tier in ('STRONG', 'LEAN'):
        # split by companion-gated (STRONG) vs bare (LEAN)
        wins = sum(1 for g in supp_grades if g['tier'] == tier and g['grade'] == 'win')
        losses = sum(1 for g in supp_grades if g['tier'] == tier and g['grade'] == 'loss')
        print(pct(wins, losses, f'supp {tier}'))

    # NRFI specifically — how the demotion would have graded:
    print()
    print('NRFI old-vs-new framing:')
    nrfi_supp_strong = [g for g in supp_grades if g['play_type'] == 'NRFI' and g['tier'] == 'STRONG']
    nrfi_supp_lean = [g for g in supp_grades if g['play_type'] == 'NRFI' and g['tier'] == 'LEAN']
    nss_w = sum(1 for g in nrfi_supp_strong if g['grade'] == 'win')
    nss_l = sum(1 for g in nrfi_supp_strong if g['grade'] == 'loss')
    nsl_w = sum(1 for g in nrfi_supp_lean if g['grade'] == 'win')
    nsl_l = sum(1 for g in nrfi_supp_lean if g['grade'] == 'loss')
    print(pct(nss_w, nss_l, 'NRFI with companion signal (would-be STRONG supp)'))
    print(pct(nsl_w, nsl_l, 'NRFI alone (would-be LEAN supp, transparency tag)'))
    print(f'  → if we had posted all {len(nrfi_supp_strong) + len(nrfi_supp_lean)} as PRIME POTD: ' +
          pct(nss_w + nsl_w, nss_l + nsl_l, 'combined').strip())

    # PRIME POTD-eligible: SIDE PRIME and TOTAL PRIME — what would have been published
    print()
    print('=' * 70)
    print('POTD-eligible PRIME calls (the ones that would have been the headline play):')
    print('=' * 70)
    prime_potd = [g for g in headline_grades if g['tier'] == 'PRIME' and g['dim'] in ('side', 'total')]
    pw = sum(1 for g in prime_potd if g['grade'] == 'win')
    pl = sum(1 for g in prime_potd if g['grade'] == 'loss')
    print(pct(pw, pl, f'PRIME POTD-eligible'))
    # show specific calls
    print('\nIndividual PRIME calls:')
    for g in sorted(prime_potd, key=lambda x: x['date'])[-15:]:
        print(f'  {g["date"]}  {g["matchup"]:46s}  {g["dim"]:5s}  {g["play_label"]:32s}  {g["grade"]:8s}  {g["detail"]}')


if __name__ == '__main__':
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    main(days)
