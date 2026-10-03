#!/usr/bin/env python3
"""Weekly external Strength-of-Record benchmark vs our computed SOR.

Andy 2026-10-03: "Is their SOR worth noting is much different from ours?
add as weekly benchmark for data."

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
strengthofrecord.com publishes an FBS Strength of Record ranking through a
clean public JSON API — api.strengthofrecord.com, no auth, robots.txt
`Allow: /`, endpoints /health, /snapshots, /snapshots/latest,
/snapshots/{year}/{week}.

This records it as a REFERENCE POINT. It is not an input to any pick, and
it is not adopted as our SOR. Three reasons, all measured on 2026 week 5:

  * It carries NO strength of schedule at all. Fields are sor, adjusted_sor,
    elo, quality_wins, movement, rivalry_wins/losses, record. /sos, /teams
    and /rankings all 404.
  * It is CFBD-derived — their own footer says "Data from CFBD", the same
    upstream we already pull. No new information enters the system.
  * Its SOR correlates +0.971 with raw win% while ours correlates +0.914.
    Theirs is CLOSER to being the record than ours is, so it carries less
    independent schedule signal, not more. Every large rank disagreement was
    a 2-2 team: theirs ranked them ~#86-105, ours ~#41-65. Theirs sorts by
    record bucket; ours separates within the bucket by who you played, which
    is the distinction that matters for a price.

Also noted and uncorrected on their side: North Dakota State (FCS) at rank
4 of an FBS ranking, four teams pinned at exactly sor=1.0000 with no
discrimination between them, and an Elo spread of 109 points across all of
FBS at week 5 (it widens by season's end — 2024 week 15 topped 1630 — so
that part is early-season design, not a flaw).

WHY KEEP IT ANYWAY
------------------
Our SOS/SOR has no external check of any kind. CFBD's own `sos` field was
supposed to be that check and is null on 0 of 808 rows across 2021-2026.
A deleted opponent inflated Miami (OH) to SOS rank 2 of 137 and went
unnoticed until Andy eyeballed it. A second opinion recorded weekly would
have shown that as a divergence, which is the entire value here: not a
better number, an independent one.

The archive runs 2014-2026 with weekly snapshots for the live season, each
carrying a content hash, so a disagreement can be replayed against the exact
snapshot that produced it.

NAME MATCHING IS THE REAL RISK
------------------------------
9 of 138 external names do not join ours (App State, Hawai'i, UConn, San
José State, Massachusetts, Florida International, UL Monroe, North Dakota
State, Arizona State). A silent mismatch DROPS a real team instead of
erroring — the exact trap compute_schedule_strength.py documents. Unmatched
names are therefore RECORDED, not quietly skipped, so the count is visible.

USAGE
    python benchmark_external_sor.py                 # latest external week
    python benchmark_external_sor.py --dry-run
    python benchmark_external_sor.py --season 2024 --week 15
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        _ln = _ln.strip()
        if _ln and not _ln.startswith('#') and '=' in _ln:
            _k, _v = _ln.split('=', 1)
            os.environ.setdefault(_k, _v.strip().strip('"'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not SB or not KEY:
    print('benchmark_external_sor: no Supabase credentials — skipping')
    sys.exit(0)
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=representation'}

API = 'https://api.strengthofrecord.com'
SOURCE = 'strengthofrecord.com'
TABLE = 'sor_benchmark'

# Their spelling -> ours. Only entries VERIFIED against both sides; a wrong
# alias is worse than a missing one because it joins the wrong team's record.
# North Dakota State is intentionally absent: it is FCS and we do not rate it,
# so it belongs in the unmatched list rather than being forced to join.
ALIASES = {
    'App State': 'Appalachian State',
    'UConn': 'Connecticut',
    "Hawai'i": 'Hawaii',
    'San José State': 'San Jose State',
    'Massachusetts': 'UMass',
    'Florida International': 'FIU',
    'UL Monroe': 'Louisiana Monroe',
}


def page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q,
                         timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ⚠ read {path} -> {r.status_code}: {(r.text or "")[:160]}')
            return out
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _corr(a: list, b: list):
    if len(a) < 3:
        return None
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    if sa == 0 or sb == 0:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / len(a) / (sa * sb)


def fetch_external(season=None, week=None) -> dict | None:
    url = (f'{API}/snapshots/{season}/{week}' if season and week
           else f'{API}/snapshots/latest')
    try:
        r = requests.get(url, timeout=30)
    except Exception as e:
        print(f'  ⚠ external fetch failed ({type(e).__name__}) — skipping')
        return None
    if r.status_code != 200:
        print(f'  ⚠ external fetch -> {r.status_code} — skipping')
        return None
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int)
    ap.add_argument('--week', type=int)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    print(f'=== benchmark_external_sor · {SOURCE} ===')
    snap = fetch_external(a.season, a.week)
    if not snap:
        return 0
    meta = snap.get('meta') or {}
    # Live snapshots use `rankings`; completed seasons use `playoff_rankings`
    # with a different rank field. Accept both rather than assuming one.
    rows = snap.get('rankings') or snap.get('playoff_rankings') or []
    if not rows:
        print('  external snapshot carried no rankings — skipping')
        return 0
    season = meta.get('year') or a.season
    week = meta.get('week') if meta.get('week') is not None else a.week
    print(f'  external: season {season} week {week} · {len(rows)} teams · '
          f'{meta.get("games_included")} games · hash '
          f'{str(meta.get("hash"))[:12]}')

    ext = {}
    for x in rows:
        t = x.get('team')
        if not t:
            continue
        ext[ALIASES.get(t, t)] = x

    ours_rows = [r for r in page('team_computed_stats',
                                 {'select': 'team,stat_key,raw_value,rank',
                                  'sport': 'eq.NCAAF',
                                  'stat_key': 'eq.sor'})]
    ours = {r['team']: r for r in ours_rows}
    print(f'  ours: {len(ours)} teams with a computed sor')

    matched = [t for t in ext if t in ours]
    unmatched = sorted(set(ext) - set(ours))
    print(f'  matched {len(matched)} · unmatched {len(unmatched)}')
    if unmatched:
        print(f'    unmatched: {unmatched}')

    if len(matched) < 20:
        print('  too few matched teams to benchmark — skipping write')
        return 0

    # Their SOR is INVERTED (lower = more impressive), so negate before
    # comparing to ours. Confirmed empirically: corr(sor, rank) = +0.981.
    th = [-float(ext[t]['sor']) for t in matched]
    ou = [float(ours[t]['raw_value']) for t in matched]
    wp = [(ext[t].get('actual_wins') or 0)
          / max(ext[t].get('games_played') or 1, 1) for t in matched]

    c_ours = _corr(th, ou)
    c_tw = _corr(th, wp)
    c_ow = _corr(ou, wp)

    their_rank = {t: ext[t].get('rank') or ext[t].get('playoff_rank')
                  for t in matched}
    our_rank = {t: ours[t].get('rank') for t in matched}
    deltas = [(abs(their_rank[t] - our_rank[t]), t) for t in matched
              if their_rank[t] and our_rank[t]]
    mad = statistics.mean([d for d, _ in deltas]) if deltas else None

    their_top10 = {t for t in matched if (their_rank[t] or 999) <= 10}
    our_top10 = {t for t in matched if (our_rank[t] or 999) <= 10}
    overlap = len(their_top10 & our_top10)

    big = []
    for d, t in sorted(deltas, reverse=True)[:10]:
        big.append({'team': t, 'theirs': their_rank[t], 'ours': our_rank[t],
                    'record': ext[t].get('record'), 'delta': d})

    def f(v):
        return None if v is None else round(float(v), 4)

    print()
    print(f'  corr(theirs, ours)        {f(c_ours)}')
    print(f'  corr(theirs, win%)        {f(c_tw)}   <- near 1.0 = just the record')
    print(f'  corr(ours,   win%)        {f(c_ow)}')
    print(f'  mean |rank delta|         {f(mad)}')
    print(f'  top-10 overlap            {overlap}/10')
    print()
    print('  biggest disagreements:')
    for b in big[:6]:
        print(f"    {b['team']:24s} theirs #{b['theirs']:<4} ours #{b['ours']:<4} "
              f"({b['delta']:+d})  rec {b['record']}")

    payload = {
        'sport': 'NCAAF', 'season': season, 'week': week, 'source': SOURCE,
        'source_hash': meta.get('hash'),
        'source_generated_at': meta.get('generated_at'),
        'teams_external': len(ext), 'teams_ours': len(ours),
        'teams_matched': len(matched),
        'teams_unmatched': unmatched,
        'corr_vs_ours': f(c_ours),
        'corr_theirs_vs_winpct': f(c_tw),
        'corr_ours_vs_winpct': f(c_ow),
        'rank_mean_abs_delta': f(mad),
        'top10_overlap': overlap,
        'biggest_disagreements': big,
    }
    if a.dry_run:
        print('\n  (dry run — nothing written)')
        return 0

    r = requests.post(f'{SB}/rest/v1/{TABLE}'
                      '?on_conflict=sport,season,week,source',
                      headers=H_W, json=[payload], timeout=60)
    if r.status_code in (200, 201):
        back = r.json() if (r.text or '').strip() else []
        print(f'\n  wrote benchmark row · verified {len(back)} row(s) returned')
        return 0
    if r.status_code == 404 or 'PGRST205' in (r.text or ''):
        print(f'\n  ℹ table {TABLE} not present yet — apply '
              f'supabase/migrations/20261003b_sor_benchmark.sql, then re-run. '
              f'The comparison above is already the useful half.')
        return 0
    print(f'\n  ⚠ write -> {r.status_code}: {(r.text or "")[:240]}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
