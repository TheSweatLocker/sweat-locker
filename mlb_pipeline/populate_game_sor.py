"""Join per-team SOR onto the game rows so the ENGINE can finally see it.

ANDY 2026-10-10: "The engine should know situations whrre SOR matters."

It cannot, today, because the number never reaches a game row. Established by
tracing rather than measuring:

  * SOR/SOS ARE computed and healthy — stat_keys sor, sos, sor_margin,
    sos_margin, sor_winpct, sos_winpct for 138 FBS teams, ranked, refreshed
    daily into team_computed_stats by compute_margin_strength.
  * ncaaf_game_context.home_sor / away_sor EXIST and are NULL on 494 of 494
    rows. Grepping the whole repo for writers of `home_sor` returns ZERO
    hits. Nothing has ever populated them.
  * ZERO of 837 signal_sources rows mention sor or sos. Of 140 NCAAF signals
    the only team-quality references are projected_spread (4) and sp_gap (4).

So SOR is a display-only number: users see it through team_computed_stats
(GameDetailV2 reads the stat_keys directly) and the engine has never once
consulted it. This script closes the data path. It does NOT create a signal —
a signal is a separate, measured decision.

WHY THIS MATTERS NOW. test_sor_conditional found SOR's information is real but
concentrated in CLOSE games, decaying monotonically with spread size:

    spread  0-3   delta vs stratified expectation  +4.39pp  (n=396)
    spread  3-7                                    +1.94pp  (n=741)
    spread  7-14                                   +0.73pp  (n=747)
    spread 14+                                     +0.23pp  (n=722)

That is a pre-registration candidate, not a proven edge (0-3 is n=396 against
a 2SE band of 5.0 — SAMPLE TOO SMALL, not null). It cannot be graded forward
at all until SOR reaches the game row, which is what this does.

⚠️ THE LEAK RULE, AND IT IS THE WHOLE REASON THIS SCRIPT IS NARROW.
team_computed_stats holds a CURRENT snapshot — refreshed_at is today. Writing
today's SOR onto a game that has already been played would stamp post-game
information onto a pre-game row and silently poison every future backtest that
reads these columns. That is exactly the trap in
project_rolling_stats_leak_trap_929, and it is unrecoverable once written
because the correct pre-game value is gone.

So this script writes ONLY games whose game_date is today or later, and
REFUSES to touch anything earlier. --all-dates does not exist on purpose.
Historical SOR must be reconstructed walk-forward (fit_srs per date), the way
test_sor_conditional does it.

Also writes sor_rank (1 = best) when the column exists, because the rank is
the scale-free form and the raw rating is ridge-compressed.

CLI
    python populate_game_sor.py --sport NCAAF --dry-run
    python populate_game_sor.py --sport NCAAF
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

CTX = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context'}

#: ncaaf_game_context spells one team differently from team_computed_stats.
#: Measured 2026-10-10: 104 of 105 teams on the upcoming slate matched by
#: exact name; FIU was the only miss. Kept as an explicit dict rather than
#: fuzzy matching, because fuzzy matching here would happily merge
#: 'Miami' with 'Miami (OH)'.
ALIASES = {
    'FIU': 'Florida International',
}


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:160]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def today_et():
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(hours=4)).strftime('%Y-%m-%d')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=('NCAAF', 'NFL'))
    ap.add_argument('--dry-run', action='store_true', dest='dry')
    a = ap.parse_args()
    sport = a.sport
    tbl = CTX[sport]
    cutoff = today_et()

    # ---- which columns does this table actually have? -------------------
    probe = _page(tbl, {'select': '*', 'limit': '1'})
    if not probe:
        print(f'! {tbl} unreadable')
        return 1
    cols = set(probe[0].keys())
    want = {}
    for side in ('home', 'away'):
        for suffix, key in (('sor', 'sor'), ('sos', 'sos'),
                            ('sor_rank', 'sor'), ('sos_rank', 'sos')):
            col = f'{side}_{suffix}'
            if col in cols:
                want[col] = (side, key, suffix.endswith('rank'))
    if not want:
        print(f'! {tbl} has no home/away sor or sos columns — nothing to '
              f'write. A migration must add them first.')
        return 1
    print(f'=== populate_game_sor · {sport}')
    print(f'    writable columns on {tbl}: {sorted(want)}')
    missing = [c for c in ('home_sos', 'away_sos', 'home_sor_rank',
                           'away_sor_rank') if c not in cols]
    if missing:
        print(f'    NOT PRESENT (needs a migration): {missing}')

    # ---- per-team SOR / SOS --------------------------------------------
    stats = _page('team_computed_stats',
                  {'select': 'team,stat_key,raw_value,rank,refreshed_at',
                   'sport': f'eq.{sport}',
                   'stat_key': 'in.(sor,sos)'})
    if not stats:
        print(f'! no sor/sos rows in team_computed_stats for {sport}')
        return 1
    by = collections.defaultdict(dict)
    for x in stats:
        by[x['stat_key']][x['team']] = x
    fresh = max(str(x.get('refreshed_at')) for x in stats)[:19]
    print(f'    team_computed_stats: sor {len(by.get("sor", {}))} teams · '
          f'sos {len(by.get("sos", {}))} teams · refreshed {fresh}')

    def look(key, team):
        """Exact name FIRST, alias only as a fallback.

        2026-10-10: order matters. compute_margin_strength now folds 'Florida
        International' into 'FIU' (they were being rated as two teams off
        partial schedules), so after that fold the stats table carries 'FIU'
        and the alias below is unnecessary. Trying the alias first would then
        look up a name that no longer exists and silently write nothing. Exact
        first means this works both before and after the fold lands.
        """
        d = by.get(key, {})
        return d.get(team) or d.get(ALIASES.get(team, team))

    # ---- UPCOMING games only. This is the leak guard. -------------------
    games = _page(tbl, {'select': 'game_id,game_date,home_team,away_team',
                        'game_date': f'gte.{cutoff}'})
    print(f'    {len(games)} games on/after {cutoff} (today ET)')
    print('    REFUSING to touch earlier games: team_computed_stats is a')
    print('    CURRENT snapshot, so writing it onto a played game would')
    print('    stamp post-game info on a pre-game row and poison every')
    print('    future backtest reading these columns, irreversibly.')

    wrote = skipped = failed = 0
    unmatched = collections.Counter()
    for g in games:
        payload = {}
        for col, (side, key, is_rank) in want.items():
            team = g.get(f'{side}_team')
            row = look(key, team)
            if row is None:
                unmatched[team] += 1
                continue
            payload[col] = (row.get('rank') if is_rank
                            else _f(row.get('raw_value')))
        payload = {k: v for k, v in payload.items() if v is not None}
        if not payload:
            skipped += 1
            continue
        if a.dry:
            wrote += 1
            if wrote <= 6:
                print(f'      [DRY] {g["away_team"]} @ {g["home_team"]}: '
                      f'{payload}')
            continue
        r = requests.patch(f'{SB}/rest/v1/{tbl}',
                           headers=H_W,
                           params={'game_id': f'eq.{g["game_id"]}'},
                           json=payload, timeout=60)
        if r.status_code in (200, 204):
            wrote += 1
        else:
            failed += 1
            print(f'      ! {g["game_id"]} {r.status_code} {r.text[:120]}')

    print(f'\n  {"would write" if a.dry else "wrote"} {wrote} · '
          f'skipped (no SOR either side) {skipped} · failed {failed}')
    if unmatched:
        print(f'  teams with no SOR row ({len(unmatched)} distinct) — '
              f'expected for non-FBS opponents:')
        for t, n in unmatched.most_common(10):
            print(f'      {t!r:<32}{n} game-sides')

    # ---- verify, because a 204 is not proof the value landed ------------
    if not a.dry and wrote:
        chk = _page(tbl, {'select': 'game_id,' + ','.join(sorted(want)),
                          'game_date': f'gte.{cutoff}'})
        filled = sum(1 for x in chk
                     if any(x.get(c) is not None for c in want))
        both = sum(1 for x in chk if x.get('home_sor') is not None
                   and x.get('away_sor') is not None)
        print(f'\n  VERIFY (re-read): {filled}/{len(chk)} rows now carry a '
              f'value · {both} have SOR on BOTH sides')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
