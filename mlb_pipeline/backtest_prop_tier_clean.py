"""B75e — re-score pre-09-22 props WITHOUT the L5 leak, and ask whether any
leak-free feature actually beats the price.

ANDY 2026-10-11: "we need a tier sytem that works, that if we say this is
Prime Jax -6.5 anytime we do that it's hitting more than not."

WHY THIS FILE EXISTS. Measured on public_receipts the same day, the live
ladder sorts by CHALK, not by edge: post-09-22 PRIME props hit 59.3%
against a 60.9% implied, i.e. they LOSE to their own price, because PRIME
lands on -150 favourites. Designing a replacement needs a backtest, and the
clean sample is only ~1,183 receipts — MLB's season is over, so it will not
grow. There are 24,057 graded props BEFORE 09-22, and their OUTCOMES are
valid (reconciled against box scores, zero mismatches —
[[project_prop_l5_leak_922]]). Only the TIER assignment was contaminated.
So the history is recoverable by recomputing the features honestly.

THE LEAK, precisely. fetch_mlb_player_recent pulled a player's whole season
game log with no upper date bound, so once the predicted game was played it
sat inside its own L5/L10 window. player_l5_hit_count feeds the LR model
that OVERRIDES tier, so tier was set partly by the outcome it predicted:

    l5_hit_count = 3  ->    6-19    24.0%
    l5_hit_count = 4  ->  966-239   80.2%
    l5_hit_count = 5  ->  308-4     98.7%   <- tautology

WHAT THIS DOES NOT DO. It never writes. backfill_prop_lookback re-tiers as
a side effect (its _promote ladder), so re-running THAT over history would
rewrite the very record we are measuring against and destroy the before /
after comparison. Every number here is computed in memory.

THE QUESTION IT ANSWERS. Not "is the model good" but the narrower,
decidable one: does a leak-free feature separate winners from losers BY
MORE THAN THE PRICE ALREADY DOES? A feature that only tracks chalk is what
we have; it is worth nothing. The control is therefore the de-vigged
implied probability on the same rows, never a flat 52.38%.

CACHE. One game-log fetch per (player, group, season), cached to disk, so a
re-run costs nothing. ~800 fetches covers 24,057 props.

CLI
    python backtest_prop_tier_clean.py --limit 2000     # quick pass
    python backtest_prop_tier_clean.py                  # everything
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

import market_line as ML
from backfill_prop_lookback import (_MLB_API_STAT, _mlb_player_id,
                                    _mlb_stat_key)

CACHE = Path(__file__).parent / '.gamelog_cache'
CUTOFF = '2026-09-22'
SEASON = 2026


# ── data ────────────────────────────────────────────────────────────────
def page(table: str, params: dict, cap: int = 80000) -> list:
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=120)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def gamelog(player: str, group: str) -> list:
    """Full season log for one player+group, newest first. Disk cached.

    Fetched with NO date bound on purpose — the bound is applied per PROP
    below, against that prop's own game_date. Caching the unbounded log is
    what turns 24,057 per-prop fetches into ~800.
    """
    CACHE.mkdir(exist_ok=True)
    safe = ''.join(c if c.isalnum() else '_' for c in player)[:60]
    fp = CACHE / f'{safe}__{group}__{SEASON}.json'
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding='utf-8'))
        except Exception:                                  # noqa: BLE001
            pass
    pid = _mlb_player_id(player)
    rows = []
    if pid:
        try:
            r = requests.get(
                f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
                params={'stats': 'gameLog', 'group': group, 'season': SEASON},
                timeout=15)
            splits = r.json().get('stats', []) if r.status_code == 200 else []
            for g in (splits[0].get('splits', []) if splits else []):
                rows.append({'date': str(g.get('date') or '')[:10],
                             'stat': g.get('stat') or {}})
        except Exception:                                  # noqa: BLE001
            rows = []
    rows.sort(key=lambda z: z['date'], reverse=True)
    try:
        fp.write_text(json.dumps(rows), encoding='utf-8')
    except Exception:                                      # noqa: BLE001
        pass
    return rows


def imp(odds) -> float | None:
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def devig(over, under) -> float | None:
    """Fair probability of the OVER once the overround is removed."""
    a, b = imp(over), imp(under)
    if a is None or b is None or (a + b) <= 0:
        return None
    return a / (a + b)


def rate(label: str, pairs: list, pad: int = 26) -> None:
    """pairs = [(won01, implied_prob)]. Always reports vs the PRICE."""
    n = len(pairs)
    if n < 40:
        print(f'    {label:<{pad}} n={n} too thin')
        return
    w = sum(p[0] for p in pairs)
    hit = w / n * 100
    mkt = statistics.fmean(p[1] for p in pairs) * 100
    se2 = (0.5 / n ** 0.5) * 100 * 2
    edge = hit - mkt
    flag = '  EDGE' if edge > 2 else ('  loses' if edge < -2 else '')
    print(f'    {label:<{pad}} {w}-{n - w}  hit {hit:5.1f}%  '
          f'mkt {mkt:5.1f}%  edge {edge:+5.1f}  n={n:<6} ±{se2:.1f}{flag}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()

    print('=== B75e · re-score pre-09-22 props without the L5 leak ===\n')
    rows = page('mlb_pipeline_props', {
        'select': 'player_name,prop_type,prop_line,direction,result,tier,'
                  'game_date,book_over_odds,book_under_odds,'
                  'player_l5_hit_count',
        'game_date': f'lt.{CUTOFF}'})
    rows = [r for r in rows
            if str(r.get('result') or '').upper() in ('WIN', 'LOSS')]
    if a.limit:
        rows = rows[:a.limit]
    print(f'  {len(rows)} graded pre-{CUTOFF} MLB props')

    # Resolve each prop to (stat_field, group); skip families with no map.
    work, nomap = [], defaultdict(int)
    for r in rows:
        sf = _mlb_stat_key(str(r.get('prop_type') or ''))
        key = _MLB_API_STAT.get(sf) if sf else None
        if not key:
            nomap[str(r.get('prop_type'))] += 1
            continue
        r['_sf'], r['_group'], r['_field'] = sf, key[0], key[1]
        work.append(r)
    print(f'  {len(work)} map to a game-log stat · '
          f'{sum(nomap.values())} do not ({len(nomap)} families)')
    if nomap:
        top = sorted(nomap.items(), key=lambda z: -z[1])[:6]
        print(f'      unmapped: {dict(top)}')

    need = sorted({(r['player_name'], r['_group']) for r in work})
    print(f'  {len(need)} distinct (player, group) logs to fetch/cache\n')
    logs, t0 = {}, time.time()
    for i, (pl, grp) in enumerate(need, 1):
        logs[(pl, grp)] = gamelog(pl, grp)
        if i % 100 == 0:
            print(f'    …{i}/{len(need)} logs  ({time.time() - t0:.0f}s)')
    print(f'  logs ready ({time.time() - t0:.0f}s)\n')

    # ── recompute the lookback honestly ──────────────────────────────────
    out = []
    for r in work:
        log = logs.get((r['player_name'], r['_group'])) or []
        gd = str(r.get('game_date'))[:10]
        try:
            line = float(r.get('prop_line'))
        except (TypeError, ValueError):
            continue
        direction = str(r.get('direction') or '').lower()
        if direction not in ('over', 'under'):
            continue
        # STRICTLY BEFORE the game being predicted. This one comparison is
        # the entire difference between the leaked feature and a real one.
        prior = [g for g in log if g['date'] and g['date'] < gd]
        vals = []
        for g in prior:
            v = (g['stat'] or {}).get(r['_field'])
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
        if len(vals) < 5:
            continue

        def hits(sub):
            return sum(1 for v in sub
                       if (v > line if direction == 'over' else v < line))

        l5_clean = hits(vals[:5])
        l10_clean = hits(vals[:10])
        season_clean = hits(vals) / len(vals)
        won = 1 if str(r['result']).upper() == 'WIN' else 0
        # Price control. Prefer a de-vigged two-way price; fall back to the
        # single side's implied, which is vigged and therefore a CONSERVATIVE
        # bar for us (it overstates the market), flagged either way.
        fair = devig(r.get('book_over_odds'), r.get('book_under_odds'))
        vigged = False
        if fair is None:
            side_odds = (r.get('book_over_odds') if direction == 'over'
                         else r.get('book_under_odds'))
            fair = imp(side_odds)
            vigged = True
        if fair is None:
            continue
        if direction == 'under' and not vigged:
            fair = 1.0 - fair        # devig() returns P(over)
        out.append({'won': won, 'p': fair, 'vigged': vigged,
                    'l5c': l5_clean, 'l10c': l10_clean, 'sc': season_clean,
                    'l5_leaked': r.get('player_l5_hit_count'),
                    'tier': str(r.get('tier') or '').upper(),
                    'n_prior': len(vals)})
    print(f'  {len(out)} props re-scored with a leak-free lookback '
          f'(needed >=5 prior games and a price)\n')
    if not out:
        print('  nothing to measure')
        return 0

    pairs = [(o['won'], o['p']) for o in out]
    print('  BASELINE on this population:')
    rate('all re-scored props', pairs)
    print(f"    de-vigged price available on "
          f"{sum(1 for o in out if not o['vigged'])}/{len(out)}; the rest "
          f"use the vigged side (a harder bar for us)")

    print('\n  THE LEAK, shown directly — stored l5_hit_count:')
    for k in range(6):
        sub = [(o['won'], o['p']) for o in out if o['l5_leaked'] == k]
        rate(f'leaked L5 = {k}', sub)

    print('\n  SAME FEATURE, recomputed honestly (games strictly before):')
    for k in range(6):
        sub = [(o['won'], o['p']) for o in out if o['l5c'] == k]
        rate(f'clean  L5 = {k}', sub)

    print('\n  clean L10:')
    for lo, hi in ((0, 3), (3, 5), (5, 7), (7, 9), (9, 11)):
        sub = [(o['won'], o['p']) for o in out if lo <= o['l10c'] < hi]
        rate(f'clean L10 {lo}-{hi - 1}', sub)

    print('\n  clean season hit rate:')
    for lo, hi in ((0, .3), (.3, .45), (.45, .55), (.55, .7), (.7, 1.01)):
        sub = [(o['won'], o['p']) for o in out if lo <= o['sc'] < hi]
        rate(f'clean season {lo:.0%}-{hi:.0%}', sub)

    print('\n  the LIVE tier on the same rows, for comparison:')
    for t in ('PRIME', 'STRONG', 'LEAN', 'COVERAGE', 'SKIP'):
        sub = [(o['won'], o['p']) for o in out if o['tier'] == t]
        rate(t, sub)

    print('\n' + '=' * 72)
    agree = sum(1 for o in out
                if o['l5_leaked'] is not None and o['l5_leaked'] == o['l5c'])
    both = sum(1 for o in out if o['l5_leaked'] is not None)
    if both:
        print(f'  stored vs clean L5 agree on {agree}/{both} '
              f'({agree / both * 100:.0f}%) — the gap is the leak')
    print('  Read every row against the `mkt` column, not against 52.38%.')
    print('  A bucket that hits 70% on 70%-implied chalk is worth nothing.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
