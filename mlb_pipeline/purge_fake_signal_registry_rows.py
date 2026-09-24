"""Delete signal_registry rows that were never signals.

signal_contribution_tracker counted every key in a prop's `signals` blob as
a signal. That blob holds fired signals AND the model inputs recorded for
the audit trail, so eight NFL input fields were calibrated and written to
signal_registry as ANTI_VALIDATED "proven money losers":

    l4  label  opp_col  opp_pct  edge_pct  games_used  season_avg
    league_baseline

All eight sat at HR 51.4% n=985 — identical, because they are present on
every graded prop, so their "hit rate" is the base rate. `league_baseline`
is a per-stat constant and `label` is a display string; neither can win or
lose a bet.

The tracker is fixed (it now drops keys present on every graded row, and
reports them). This removes the rows that fix cannot retract.

SAFETY. A row is only deleted when ALL of these hold:
  - no signal_sources row anywhere has that signal_name, so nothing can
    look it up for a weight
  - the key is present on 100% of graded prop rows for that sport, checked
    live against the prop table rather than taken from this list
  - it is a prop-scope row (market_scope='prop' or '*')

Anything failing a check is reported and left alone. Dry by default.
"""
import os
import sys
import argparse
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

PROP_TABLE = {
    'NFL': 'nfl_pipeline_props',
    'MLB': 'mlb_pipeline_props',
    'NBA': 'nba_pipeline_props',
    'NHL': 'nhl_pipeline_props',
}


def page(path: str, params: dict) -> list:
    out, offset = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, timeout=180,
                         params={**params, 'order': 'id.asc',
                                 'limit': 1000, 'offset': offset})
        r.raise_for_status()
        b = r.json()
        out.extend(b)
        if len(b) < 1000:
            return out
        offset += 1000


def universal_keys(sport: str) -> tuple[set, int]:
    """Keys present on EVERY graded prop row for this sport."""
    table = PROP_TABLE.get(sport)
    if not table:
        return set(), 0
    rows = page(table, {'select': 'signals,result'})
    graded = [r for r in rows
              if str(r.get('result') or '').strip().upper()
              in ('WIN', 'W', 'LOSS', 'L')
              and isinstance(r.get('signals'), dict)]
    if not graded:
        return set(), 0
    counts: dict = {}
    for r in graded:
        for k in r['signals']:
            if k.startswith('_'):
                continue
            counts[k] = counts.get(k, 0) + 1
    return {k for k, c in counts.items() if c >= len(graded)}, len(graded)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    live_names = {s['signal_key'] for s in
                  page('signal_sources', {'select': 'signal_key'})}
    reg = page('signal_registry',
               {'select': 'id,signal_name,sport,market_scope,hit_rate,'
                          'sample_n,tier'})
    print(f'signal_sources names: {len(live_names)}   '
          f'signal_registry rows: {len(reg)}\n')

    doomed, kept = [], []
    cache: dict = {}
    for row in reg:
        sport = row.get('sport') or ''
        name = row['signal_name']
        scope = row.get('market_scope') or ''
        if name in live_names:
            continue                      # a real signal — never touch
        if scope not in ('prop', '*'):
            continue
        if sport not in cache:
            cache[sport] = universal_keys(sport)
            print(f'  {sport}: {len(cache[sport][0])} universal keys over '
                  f'{cache[sport][1]} graded props')
        uni, n_graded = cache[sport]
        if name in uni:
            doomed.append(row)
        else:
            kept.append(row)

    print(f'\nfake (orphan AND present on every graded row): {len(doomed)}')
    print(f"  {'signal_name':20s} {'sport':6s} {'scope':7s} {'HR':>6s} "
          f"{'n':>6s} tier")
    for r in doomed:
        hr = r.get('hit_rate')
        hr_s = f'{hr:.1f}' if hr is not None else '--'
        print(f"  {r['signal_name'][:19]:20s} {str(r.get('sport')):6s} "
              f"{str(r.get('market_scope')):7s} {hr_s:>6s} "
              f"{int(r.get('sample_n') or 0):>6d} {r.get('tier')}")

    print(f'\norphan registry rows KEPT (fire on a subset — could be real '
          f'legacy signals): {len(kept)}')
    for r in sorted(kept, key=lambda z: -(z.get('sample_n') or 0))[:10]:
        print(f"  {r['signal_name'][:19]:20s} {str(r.get('sport')):6s} "
              f"n={int(r.get('sample_n') or 0)} {r.get('tier')}")

    if not args.write:
        print(f'\nDRY RUN — {len(doomed)} row(s) would be deleted. '
              f'Re-run with --write.')
        return

    ok = 0
    for r in doomed:
        d = requests.delete(f'{SB}/rest/v1/signal_registry', headers=H,
                            timeout=60, params={'id': f"eq.{r['id']}"})
        if d.status_code in (200, 204):
            ok += 1
        else:
            print(f"  !! id={r['id']} {d.status_code} {d.text[:120]}")
    print(f'\ndeleted {ok}/{len(doomed)}')


if __name__ == '__main__':
    main()
