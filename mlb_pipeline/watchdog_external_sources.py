"""Externals watchdog — catch a source that has silently stopped producing.

WHY THIS EXISTS
---------------
Andy 2026-09-25: "concur with some kind of external watchdog especially with
NFL." The audit that prompted it found, for NFL alone:

  * vsin + bettingpros: 89 pull attempts each, ZERO picks all season. Their
    fetchers are literal stubs (`return [], 200`).
  * dimers + action: real parsers, dark since 09-17, because the NFL/NCAAF
    workflows never installed Playwright — render_page returned 'unavailable',
    the fetchers treated it as a graceful skip, and the pull log wrote
    status='success'.
  * oddscrowd: 23 game pages fetched HTTP 200, 0 picks — the site moved to
    client-render and the plain-requests parser now matches nothing.

Every one of those logged `status='success'`. Nothing could alert. This is the
same silent-failure class as the 104 `|| echo` masks closed on 09-24: the
defect announced itself as green.

THE DISCRIMINATOR IS PEER-RELATIVE — this is the whole design.
A source returning 0 picks is NOT a failure. No slate, an off-day, a Tuesday in
football, picks not posted yet (Andy: PickDawgz posts by Friday) — all produce
a legitimate 0. A watchdog that fires on any zero would cry wolf every Tuesday,
get ignored, and be worse than nothing.

So a day only counts against a source when it pulled 0 **while another source
on the same sport pulled picks** — evidence a slate existed and this source
alone missed it.

Then the measure is the GAP since the source last produced, in those
peer-productive days — not a consecutive-miss streak. Streak counting looked
right and was wrong: football touts publish on different days of the week, so
healthy weekly sources accumulate long peer-relative miss runs as a matter of
course (scoresandodds and covers each show runs of 8). A fixed streak threshold
would have alerted on both. A gap resets whenever the source posts, so it
adapts to any cadence on its own.

The threshold is a flat DARK_GAP_FLOOR. Calibrating it to each source's own
historical worst gap was tried and is wrong: dimers and action had been broken
for most of the window, so their bad history became their baseline and both
scored "healthy" at an 8-day gap. A source that is already down cannot supply
its own definition of normal. Its historical gap is still printed as context,
and --dark-days raises the floor if a genuinely weekly-only tout needs it.

Deliberately NOT flagged:
  * days where every source got 0 (no slate — nothing to miss)
  * sports with no recent pull activity at all (offseason)
  * a source inside its own normal cadence, however sparse
A source that has never produced at all is reported separately as NEVER, since
that is the stub signature rather than a regression.

USAGE
    python watchdog_external_sources.py                    # all sports, 14d
    python watchdog_external_sources.py --sport NFL --days 21
    python watchdog_external_sources.py --strict           # exit 1 on alert
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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

# Floor for the peer-productive gap that calls a source dark. The real
# threshold is per-source: max(this floor, the source's own historical worst
# gap) — see the cadence note in main().
DARK_GAP_FLOOR = 5


def page(path: str, params: dict) -> list:
    """Paginate. PostgREST caps at 1000 rows and says so only in a header, so a
    bare request silently truncates — the documented truncation class."""
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=p, timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ERROR {r.status_code} on {path}: {(r.text or "")[:200]}')
            sys.exit(2)
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None, help='limit to one sport')
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--dark-days', type=int, default=None,
                    help='override the gap floor (default 5; the effective '
                         "threshold is still raised to a source's own "
                         'historical worst gap)')
    ap.add_argument('--strict', action='store_true',
                    help='exit 1 if any source is dark (for CI gating)')
    args = ap.parse_args()

    since = ((datetime.now(timezone.utc) - timedelta(hours=4)).date()
             - timedelta(days=args.days)).isoformat()
    params = {'select': 'sport,source,picks_pulled,started_at,status,'
                        'error_message,http_status',
              'started_at': f'gte.{since}', 'order': 'id.asc'}
    if args.sport:
        params['sport'] = f'eq.{args.sport.upper()}'
    rows = page('external_pull_log', params)

    print(f'=== externals watchdog · last {args.days}d (since {since})')
    print(f'    {len(rows)} pull attempts')
    if not rows:
        print('    nothing to check.')
        return

    # (sport, date) -> source -> picks pulled that day
    byday = defaultdict(lambda: defaultdict(int))
    attempts = defaultdict(int)
    errors = defaultdict(set)
    for r in rows:
        sp, src = r.get('sport'), r.get('source')
        if not sp or not src:
            continue
        d = (r.get('started_at') or '')[:10]
        if not d:
            continue
        byday[(sp, d)][src] += (r.get('picks_pulled') or 0)
        attempts[(sp, src)] += 1
        if r.get('error_message'):
            errors[(sp, src)].add(str(r['error_message'])[:80])

    alerts, nevers, healthy = [], [], []
    sports = sorted({sp for sp, _ in byday})
    for sp in sports:
        dates = sorted(d for s, d in byday if s == sp)
        srcs = sorted({s for d in dates for s in byday[(sp, d)]})
        for src in srcs:
            total = sum(byday[(sp, d)].get(src, 0) for d in dates)
            # A "peer-productive miss" is a day this source pulled 0 while some
            # OTHER source on the same sport pulled picks — evidence a slate
            # existed and this source alone missed it.
            #
            # Counting CONSECUTIVE misses is wrong for weekly sports. Football
            # touts publish on different days (some Thursday, some Sunday), so a
            # healthy weekly source racks up long peer-relative miss runs as a
            # matter of course — scoresandodds and covers both show runs of 8.
            # A fixed streak threshold would have alerted on both.
            #
            # What actually separates broken from weekly is the gap since the
            # source LAST produced, measured in peer-productive days. A source
            # keeping its own cadence resets that counter every time it posts,
            # whatever the cadence is. And the threshold is calibrated per
            # source: its own historical worst gap, floored. A source is only
            # dark once it is quieter than it has ever been.
            gaps, cur, last_ok, misses = [], 0, None, 0
            for d in dates:
                day = byday[(sp, d)]
                if src not in day:
                    continue          # not attempted — not a miss
                peers = sum(v for k, v in day.items() if k != src)
                if day.get(src, 0) > 0:
                    gaps.append(cur)
                    cur = 0
                    last_ok = d
                elif peers > 0:       # slate existed, we alone got nothing
                    cur += 1
                    misses += 1
                # else: nobody got picks — no slate, not a miss
            hist = max(gaps) if gaps else 0
            # Threshold is the flat floor, NOT max(floor, hist). Calibrating to
            # the source's own worst historical gap sounds better and fails on
            # exactly the sources that matter: dimers and action had been broken
            # for most of the window, so their own bad history became their
            # baseline and both were scored "healthy" at an 8-day gap. A source
            # already down cannot supply its own definition of normal.
            # hist is kept and printed as context for judging a real weekly-only
            # tout, which is what --dark-days is for.
            thresh = args.dark_days or DARK_GAP_FLOOR
            rec = {'sport': sp, 'source': src, 'total': total,
                   'gap': cur, 'hist': hist, 'thresh': thresh,
                   'misses': misses, 'last_ok': last_ok,
                   'attempts': attempts[(sp, src)], 'errs': errors[(sp, src)]}
            if total == 0 and misses:
                nevers.append(rec)
            elif cur > thresh:
                alerts.append(rec)
            else:
                healthy.append(rec)

    def fmt(r):
        e = f"\n{'':20s}err: {list(r['errs'])[0]}" if r['errs'] else ''
        return (f"    {r['sport']:6s} {r['source']:14s} "
                f"picks={r['total']:5d} attempts={r['attempts']:4d} "
                f"gap={r['gap']:2d}d (own worst {r['hist']}d, "
                f"alerts over {r['thresh']}d) "
                f"last_ok={r['last_ok'] or 'NEVER':>10s}{e}")

    if nevers:
        print(f'\n  🚨 NEVER PRODUCED ({len(nevers)}) — zero picks while peers '
              f'had a slate. Stub fetcher or dead parser:')
        for r in sorted(nevers, key=lambda x: (x['sport'], x['source'])):
            print(fmt(r))
    if alerts:
        print(f'\n  🚨 DARK ({len(alerts)}) — produced before, now missing '
              f'slates its peers are covering:')
        for r in sorted(alerts, key=lambda x: -x['gap']):
            print(fmt(r))
    if not nevers and not alerts:
        print('\n  ✅ no source is dark. Every source that missed a day missed '
              'it on a day nobody had picks.')

    print(f'\n  healthy ({len(healthy)}):')
    for r in sorted(healthy, key=lambda x: (x['sport'], -x['total'])):
        print(f"    {r['sport']:6s} {r['source']:14s} picks={r['total']:5d}  "
              f"gap={r['gap']}d  last_ok={r['last_ok'] or 'NEVER'}")

    n = len(nevers) + len(alerts)
    print(f'\n  VERDICT: {n} source(s) need attention, {len(healthy)} healthy.')
    if n and args.strict:
        sys.exit(1)


if __name__ == '__main__':
    main()
