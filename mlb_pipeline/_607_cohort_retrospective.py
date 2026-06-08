"""
Retrospective: what would yesterday (6/7) have looked like if we ran the
cohort signals system? Pull every game, identify what the system would
have backed (LOCK + STRONG_EDGE on matched side) vs faded (FADE on
matched side = take the other side), score against actual outcomes.

CAVEAT: cohort rules were computed on data INCLUDING 6/7 outcomes, so
this is slightly self-referential — but 1 day in 518 has minimal influence
on the cohort statistics. Reasonable validation, not a true out-of-sample
test. To do an out-of-sample version, would re-run refresh_cohort_signals
filtering to game_date < '2026-06-07' and then apply.
"""
import os, requests, json, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from cohort_signals import evaluate_game_for_play

URL = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey':KEY, 'Authorization':f'Bearer {KEY}'}


def _f(v):
    try: return float(v)
    except: return None


def actual_ml(g):
    return 'home' if g.get('home_win') else 'away'


def actual_rl(g):
    hs = g.get('home_score'); as_ = g.get('away_score')
    if hs is None or as_ is None: return None
    m = abs(hs - as_)
    if m <= 1: return 'push'
    return 'home' if hs > as_ else 'away'


def actual_total(g):
    line = g.get('close_total') or g.get('open_total')
    hs = g.get('home_score'); as_ = g.get('away_score')
    if line is None or hs is None: return None
    t = hs + as_
    if t > line: return 'over'
    if t < line: return 'under'
    return 'push'


def ml_call(s):
    if s is None or abs(s) < 0.3: return None
    return 'home' if s > 0 else 'away'


def rl_call(s):
    if s is None: return None
    if s > 1.5: return 'home'
    if s < -1.5: return 'away'
    return 'away' if s < 0 else 'home'


def conf_side(net):
    try: n = int(net)
    except: return None
    if n > 1: return 'home'
    if n < -1: return 'away'
    return None


def total_call(mt, line):
    if mt is None or line is None: return None
    if mt >= line + 0.7: return 'over'
    if mt <= line - 0.7: return 'under'
    return None


PLAY_CALL = {
    'v3_ml':    lambda g: ml_call(_f(g.get('projected_spread'))),
    'v4_ml':    lambda g: ml_call(_f(g.get('model_pred_spread'))),
    'jerry_ml': lambda g: ml_call(_f(g.get('jerry_pred_spread'))),
    'conf_ml':  lambda g: conf_side(g.get('signal_confluence_net')),
    'v3_rl':    lambda g: rl_call(_f(g.get('projected_spread'))),
    'v4_rl':    lambda g: rl_call(_f(g.get('model_pred_spread'))),
    'jerry_rl': lambda g: rl_call(_f(g.get('jerry_pred_spread'))),
    'conf_rl':  lambda g: conf_side(g.get('signal_confluence_net')),
    'v3_tot':   lambda g: total_call(_f(g.get('projected_total')), _f(g.get('close_total')) or _f(g.get('open_total'))),
    'v4_tot':   lambda g: total_call(_f(g.get('model_pred_total')), _f(g.get('close_total')) or _f(g.get('open_total'))),
    'jerry_tot':lambda g: total_call(_f(g.get('jerry_pred_total')), _f(g.get('close_total')) or _f(g.get('open_total'))),
}

ACTUAL_FN = {
    'v3_ml': actual_ml, 'v4_ml': actual_ml, 'jerry_ml': actual_ml, 'conf_ml': actual_ml,
    'v3_rl': actual_rl, 'v4_rl': actual_rl, 'jerry_rl': actual_rl, 'conf_rl': actual_rl,
    'v3_tot': actual_total, 'v4_tot': actual_total, 'jerry_tot': actual_total,
}


def main():
    r = requests.get(f'{URL}/rest/v1/mlb_game_results?game_date=eq.2026-06-07&select=*&order=home_team', headers=H).json()
    games = [g for g in r if g.get('home_score') is not None]
    print(f'=== 6/7 RETROSPECTIVE COHORT BACKTEST  ({len(games)} games) ===')
    print()

    locks_tally = {'w':0, 'l':0, 'p':0}
    strongs_tally = {'w':0, 'l':0, 'p':0}
    fades_tally = {'w':0, 'l':0, 'p':0}

    system_plays = []
    system_fades = []

    for g in sorted(games, key=lambda x: x.get('home_team','')):
        away = g.get('away_team','')[:18]
        home = g.get('home_team','')[:18]
        hs = g.get('home_score'); as_ = g.get('away_score')

        for play in PLAY_CALL:
            call = PLAY_CALL[play](g)
            if call is None: continue
            actual = ACTUAL_FN[play](g)
            if actual is None: continue

            matches = evaluate_game_for_play(g, play, direction=call)
            if not matches: continue
            top = matches[0]
            tier = top['tier']
            result = 'P' if actual == 'push' else ('W' if call == actual else 'L')

            if tier == 'LOCK':
                if result == 'W': locks_tally['w'] += 1
                elif result == 'L': locks_tally['l'] += 1
                else: locks_tally['p'] += 1
                system_plays.append((play, call, away, home, f'{as_}-{hs}', tier, top['matches_if_raw'], top['shrunken_pct'], result))
            elif tier == 'STRONG_EDGE':
                if result == 'W': strongs_tally['w'] += 1
                elif result == 'L': strongs_tally['l'] += 1
                else: strongs_tally['p'] += 1
                system_plays.append((play, call, away, home, f'{as_}-{hs}', tier, top['matches_if_raw'], top['shrunken_pct'], result))
            elif tier in ('FADE', 'HARD_FADE'):
                # FADE means "system says do NOT take this side". A fade-bet
                # wins when the system-flagged side LOSES.
                if result == 'L': fades_tally['w'] += 1
                elif result == 'W': fades_tally['l'] += 1
                else: fades_tally['p'] += 1
                system_fades.append((play, call, away, home, f'{as_}-{hs}', tier, top['matches_if_raw'], top['shrunken_pct'], result))

    print('SYSTEM-RECOMMENDED PLAYS  (LOCK + STRONG_EDGE on the called side)')
    print(f'  {"PLAY":<10} {"CALL":<5} {"GAME":<42} {"FINAL":<7} {"TIER":<11} {"COHORT":<40} {"SHRUNK":<7} OUTCOME')
    for p in system_plays:
        print(f'  {p[0]:<10} {p[1]:<5} {p[2]:<18} @ {p[3]:<18} {p[4]:<7} {p[5]:<11} {p[6]:<40} {p[7]:<7}%  {p[8]}')

    print()
    print('SYSTEM-FLAGGED FADES  (FADE/HARD_FADE — bet AGAINST this side)')
    print(f'  {"PLAY":<10} {"CALL":<5} {"GAME":<42} {"FINAL":<7} {"TIER":<11} {"COHORT":<40} {"SHRUNK":<7} OUTCOME')
    for p in system_fades:
        print(f'  {p[0]:<10} {p[1]:<5} {p[2]:<18} @ {p[3]:<18} {p[4]:<7} {p[5]:<11} {p[6]:<40} {p[7]:<7}%  {p[8]}')

    def pct(t):
        n = t['w'] + t['l']
        return f"{t['w']}-{t['l']}-{t['p']}P  ({100*t['w']/n:.1f}%)" if n else "0-0"

    print()
    print('TALLY')
    print(f'  LOCK plays:        {pct(locks_tally)}')
    print(f'  STRONG_EDGE plays: {pct(strongs_tally)}')
    print(f'  FADE plays:        {pct(fades_tally)}  (W = side correctly faded)')
    combined = {
        'w': locks_tally['w'] + strongs_tally['w'] + fades_tally['w'],
        'l': locks_tally['l'] + strongs_tally['l'] + fades_tally['l'],
        'p': locks_tally['p'] + strongs_tally['p'] + fades_tally['p'],
    }
    print(f'  COMBINED:          {pct(combined)}')


if __name__ == '__main__':
    main()
