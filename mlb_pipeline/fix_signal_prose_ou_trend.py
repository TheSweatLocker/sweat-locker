"""Fix the O/U trend prose: UNDER label reading an OVER value, and no sample.

WHAT ANDY SAW (2026-09-25, QA screenshot, ARI @ SF)
    "Under 47.5: ARI games UNDER 0.00% overs this season"

Two defects in one sentence.

1. LABEL/VALUE MISMATCH. The template is
       '{away_team} games UNDER {away_season_over_pct}% overs this season'
   It says UNDER and then interpolates the OVER percentage. ARI is 0 overs /
   2 unders, so over_pct is legitimately 0.0 and the sentence renders as
   "UNDER 0.00%" — which reads as missing data. Jerry's read on the same card
   states the same fact correctly ("the Cardinals have gone Under 100%"), so
   the card contradicted itself.

2. NO SAMPLE SIZE. "0.00%" is two decimals of false precision on a TWO GAME
   sample. The standing rule is that every percentage shows its n, and the
   O/U counts (home_season_ou_overs / _ou_unders) were already available as
   template variables — they just were not used.

New shape keeps the label honest and puts the sample in front of the reader:
    '{away_team} overs in {away_season_over_pct}% of games this season (0O-2U)'

The OVER-side templates are relabelled too. Their label already matched their
value, but they carried a bare percentage with no n for the same reason.

WHY THIS IS A SCRIPT AND NOT A ONE-OFF: this exact bug was fixed for NCAAF
alone on 2026-09-05 (_fix_ncaaf_season_prose_2026_09_05.py, a gitignored
scratch file) and left in place for MLB, NBA, NCAAB, NFL and NHL. Fixing one
sport's row and moving on is what let it ship to paying users three weeks
later. This walks every sport.

VERIFIED BEFORE WRITING: home/away_season_ou_overs and _ou_unders exist on
mlb_game_context, nfl_game_context, ncaaf_game_context, nba_game_context and
ncaab_game_context. A template variable that does not resolve renders blank,
so the columns were probed per sport rather than assumed.

    python fix_signal_prose_ou_trend.py            # dry run
    python fix_signal_prose_ou_trend.py --commit
"""
import argparse
import os
import re
import sys

import requests

sys.stdout.reconfigure(encoding='utf-8')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Sports whose context table was confirmed to carry the O/U count columns.
# NHL is absent from this set on purpose — it has no *_season_ou_* columns at
# all, so a count in its prose would render blank. It also has no
# *_trend_season rows, so there is nothing to fix there.
SPORTS_WITH_COUNTS = {'MLB', 'NFL', 'NCAAF', 'NCAAB', 'NBA'}


def window_phrase(tpl: str) -> str:
    """Preserve whatever window the existing row claims.

    NCAAF was moved to 'trailing L13' on 09-05 because its numbers are a
    rolling window, not a season. Rewriting that back to 'this season' would
    re-introduce the very bug that fix addressed, so the window wording is
    carried over rather than standardised.
    """
    low = tpl.lower()
    if 'trailing l13' in low:
        return 'trailing L13'
    if 'this season' in low:
        return 'this season'
    return 'this season'


def build(signal_key: str, tpl: str) -> str | None:
    """Return the replacement template, or None to leave the row alone."""
    m = re.match(r'(home|away)_team_(under|over)_trend_season$', signal_key)
    if not m:
        return None
    side, direction = m.group(1), m.group(2)
    win = window_phrase(tpl)
    pct = f'{{{side}_season_over_pct}}'
    o = f'{{{side}_season_ou_overs}}'
    u = f'{{{side}_season_ou_unders}}'
    team = f'{{{side}_team}}'
    # Lead with the O-U record. It carries the sample and the direction at a
    # glance ("0O-2U" says all-unders without needing a percentage at all),
    # and the parenthetical labels the percentage as an OVER rate, which is
    # the number we actually have. Nothing can be misread the way
    # "UNDER 0.00% overs" was.
    #
    # The only difference between directions is the qualifier. That matters:
    # an earlier pass had both render identical text, which meant an UNDER
    # pick printed a sentence that read as an argument for the over.
    qualifier = 'just ' if direction == 'under' else ''
    return f'{team} {o}O-{u}U {win} (overs in {qualifier}{pct}% of games)'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--commit', action='store_true')
    args = ap.parse_args()

    r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H, timeout=60,
                     params={'select': 'signal_key,sport,display_prose_template',
                             'order': 'sport.asc,signal_key.asc',
                             'limit': '2000'})
    if r.status_code not in (200, 206):
        print(f'fetch failed {r.status_code} {(r.text or "")[:200]}')
        sys.exit(1)
    rows = r.json()
    print(f'=== signal_sources: {len(rows)} rows\n')

    plan, skipped = [], []
    for x in rows:
        sk = x['signal_key']
        sp = x['sport']
        tpl = x.get('display_prose_template') or ''
        new = build(sk, tpl)
        if not new or new == tpl:
            continue
        if sp not in SPORTS_WITH_COUNTS:
            skipped.append((sp, sk, 'no *_season_ou_* columns on its context table'))
            continue
        plan.append((sp, sk, tpl, new))

    if not plan:
        print('  nothing to change.')
        return

    for sp, sk, old, new in plan:
        print(f'  {sp:6s} {sk}')
        print(f'      -  {old}')
        print(f'      +  {new}')
    if skipped:
        print('\n  skipped:')
        for sp, sk, why in skipped:
            print(f'    {sp:6s} {sk}  ({why})')

    print(f'\n  {len(plan)} row(s) to patch.')
    if not args.commit:
        print('  DRY RUN — add --commit to write.')
        return

    ok = 0
    for sp, sk, _old, new in plan:
        pr = requests.patch(
            f'{SB}/rest/v1/signal_sources', headers=H_W, timeout=30,
            params={'signal_key': f'eq.{sk}', 'sport': f'eq.{sp}'},
            json={'display_prose_template': new})
        good = pr.status_code in (200, 204)
        ok += good
        print(f'  {"OK " if good else "FAIL"} {sp:6s} {sk}  -> {pr.status_code}')
    print(f'\n  patched {ok}/{len(plan)}. Next context generation uses the new '
          f'prose; already-generated rows keep the old text until regenerated.')


if __name__ == '__main__':
    main()
