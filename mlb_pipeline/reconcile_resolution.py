"""Cross-sport resolution reconciliation — READ-ONLY detector.

Andy 2026-09-18: resolving is the weak area. Every resolution bug found
that day (NFL C/ATT graded off 0.0, MLB top_8 picks stuck Pending on
line drift, the POTD hardcoded to sport=MLB, NCAAF duplicate ctx rows,
two NCAAF games final with nothing graded) was found because a human
noticed. There was no detector. This is the detector.

Writes NOTHING. Answers one question per check: is there a pick whose
stored state cannot be true?

CHECKS
  1 STALE      picks on a past date still ungraded
  2 ZEROVAL    props graded against final_value NULL or 0.0
  3 DANGLING   sweat_card top_8 picks whose source row does not exist
  4 ORPHANDATE props on a game_date with no game context rows
  5 DUPCTX     one matchup with multiple context rows

Exit 0 clean, 1 if any CRITICAL fired. Safe to gate a pipeline step on.

    python reconcile_resolution.py
    python reconcile_resolution.py --days 10 --sport NFL
    python reconcile_resolution.py --quiet      # summary only
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# sport -> (context table, props table or None)
SPORTS = {
    'MLB':   ('mlb_game_context',   'mlb_pipeline_props'),
    'NFL':   ('nfl_game_context',   'nfl_pipeline_props'),
    'NCAAF': ('ncaaf_game_context', None),   # no props, per Andy
    'NCAAB': ('ncaab_game_context', None),
    'NBA':   ('nba_game_context',   None),
    'NHL':   ('nhl_game_context',   None),
}

UNGRADED = (None, '', 'Pending', 'PENDING')

# Views that represent "what a user could actually see". Grading noise on
# banned-family rows is not a defect worth alerting on.
PUBLISHABLE_VIEW = {
    'MLB': 'v_mlb_props_publishable',
    'NFL': 'v_nfl_props_publishable',
}

# Stats where a value of 0 is IMPLAUSIBLE for a player who appeared, so a
# stored 0.0 means the stat was never read. A pitcher who took the mound
# records outs; a QB who threw for yards attempted passes. Batter counting
# stats (hr, rbis, runs, hits, total_bases, sb) are legitimately zero most
# of the time and must NOT be listed here.
_ZERO_IMPLAUSIBLE = (
    'outs', 'pass_attempts', 'pass_completions',
)


def _zero_implausible(prop_type: str) -> bool:
    return any(s in prop_type for s in _ZERO_IMPLAUSIBLE)


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def page(table: str, select: str, extra: str = '', cap: int = 20000) -> list:
    """Paged GET. Raises on error rather than returning [] — a swallowed
    error reads as 'no problems found', which is the failure mode this
    whole script exists to catch."""
    out, off = [], 0
    while off < cap:
        r = requests.get(f'{SB}/rest/v1/{table}?select={select}{extra}'
                         f'&limit=1000&offset={off}', headers=H, timeout=40)
        if r.status_code != 200:
            raise RuntimeError(f'{table} [{select[:40]}] -> {r.status_code}: {r.text[:180]}')
        batch = r.json()
        if not batch: break
        out.extend(batch)
        if len(batch) < 1000: break
        off += 1000
    return out


class Report:
    def __init__(self):
        self.findings: list[tuple[str, str, str, list]] = []

    def add(self, sev: str, check: str, headline: str, rows: list | None = None):
        self.findings.append((sev, check, headline, rows or []))

    @property
    def critical(self) -> int:
        return sum(1 for f in self.findings if f[0] == 'CRITICAL')


def check_stale(rep: Report, days: int, sport_filter: str | None):
    """Picks on a date that has passed, still carrying no result."""
    cutoff = today_et()
    start = (datetime.fromisoformat(cutoff) - timedelta(days=days)).date().isoformat()

    reads = page('jerry_reads', 'sport,game_date,game_id,call_market,call_text,conviction,result',
                 f'&game_date=gte.{start}&game_date=lt.{cutoff}')
    by_sport = defaultdict(list)
    for x in reads:
        sp = (x.get('sport') or '?').upper()
        if sport_filter and sp != sport_filter: continue
        if (x.get('call_market') or '') == 'pass': continue   # a Pass has nothing to grade
        if x.get('result') in UNGRADED:
            by_sport[sp].append(x)
    for sp, rows in sorted(by_sport.items()):
        rep.add('CRITICAL', 'STALE',
                f'{sp}: {len(rows)} jerry_reads on past dates still ungraded', rows)

    # Props: only count what a USER COULD SEE. The raw tables carry
    # thousands of banned-family rows (total_bases_under, rbis_under, ...)
    # that never publish, so grading them is noise — counting them made
    # this check report 866 when the real number was 69. Read the
    # publishable view where one exists; fall back to tier on the raw
    # table only for sports that have no view.
    for sport, (_ctx, props) in SPORTS.items():
        if not props: continue
        if sport_filter and sport != sport_filter: continue
        view = PUBLISHABLE_VIEW.get(sport)
        src = view or props
        rows = page(src, 'game_date,player_name,prop_type,prop_line,direction,tier,result',
                    f'&game_date=gte.{start}&game_date=lt.{cutoff}&result=is.null')
        live = rows if view else [x for x in rows
                                  if (x.get('tier') or '').upper() in ('PRIME', 'STRONG', 'LEAN')]
        if not live: continue
        by_date = Counter(x['game_date'] for x in live)
        # Yesterday can legitimately still be mid-grade; older cannot.
        yday = (datetime.fromisoformat(cutoff) - timedelta(days=1)).date().isoformat()
        older = {d: n for d, n in by_date.items() if d < yday}
        sev = 'CRITICAL' if older else 'WARN'
        note = '' if older else ' (all from yesterday — may still be grading)'
        rep.add(sev, 'STALE',
                f'{sport}: {len(live)} PUBLISHED props ungraded{note} — '
                f'by date {dict(sorted(by_date.items()))}', live)


def check_zeroval(rep: Report, days: int, sport_filter: str | None):
    """Props carrying a result that was decided against 0.0 or NULL."""
    cutoff = today_et()
    start = (datetime.fromisoformat(cutoff) - timedelta(days=days)).date().isoformat()
    for sport, (_ctx, props) in SPORTS.items():
        if not props: continue
        if sport_filter and sport != sport_filter: continue
        rows = page(props, 'game_date,player_name,prop_type,prop_line,direction,tier,result,final_value',
                    f'&game_date=gte.{start}&result=not.is.null')
        # A row carrying a REAL verdict with no value was decided against
        # nothing. But 'Void' (player did not play) and 'UNGRADEABLE' are
        # honest not-known states and SHOULD have a null value — flagging
        # them would punish the resolver for admitting it could not grade,
        # which is the behaviour we actually want.
        nulls = [x for x in rows
                 if x.get('final_value') is None
                 and (x.get('result') or '') in ('Win', 'Loss', 'Push')]
        # A 0.0 is only suspicious where zero is IMPLAUSIBLE for a player who
        # appeared. Batter counting stats are mostly zero in real life —
        # hr_over was 148/163 zeros on 9/17 and every one of those is correct.
        # Flagging those made this check report 63.7% and cry wolf.
        zeros = [x for x in rows
                 if x.get('final_value') in (0, 0.0)
                 and _zero_implausible(x.get('prop_type') or '')]
        if nulls:
            rep.add('CRITICAL', 'ZEROVAL',
                    f'{sport}: {len(nulls)}/{len(rows)} graded props have final_value NULL — '
                    f'graded against no value at all — '
                    f'{dict(Counter(x["prop_type"] for x in nulls).most_common(4))}', nulls)
        if zeros:
            pct = 100 * len(zeros) / max(len(rows), 1)
            rep.add('CRITICAL', 'ZEROVAL',
                    f'{sport}: {len(zeros)} graded props have final_value 0.0 on a stat that '
                    f'cannot be zero if the player appeared ({pct:.1f}%) — '
                    f'{dict(Counter(x["prop_type"] for x in zeros).most_common(4))}', zeros)


def check_dangling(rep: Report, days: int, sport_filter: str | None):
    """sweat_card top_8 entries whose source row cannot be found."""
    if sport_filter and sport_filter != 'MLB':
        return
    cards = page('jerry_cache', 'cache_key,data', '&cache_key=like.sweat_card_%&sport=eq.MLB'
                                                  '&order=cache_key.desc', cap=days + 2)
    cutoff = today_et()
    bad = []
    for c in cards[:days + 1]:
        slate = c['cache_key'].replace('sweat_card_', '')
        if slate >= cutoff: continue
        data = c.get('data') or {}
        if isinstance(data, str):
            try: data = json.loads(data)
            except Exception:
                bad.append({'slate': slate, 'why': 'card JSON unparseable'}); continue
        for p in (data.get('top_8') or []):
            if p.get('source_table') != 'mlb_pipeline_props': continue
            if p.get('source_id'):  continue   # stable handle, trust it
            # A broken ref on a pick that ALREADY has a result is history,
            # not a defect — the grade landed by another route. Only an
            # ungraded pick with a dead reference is actionable, because
            # that one can never resolve on its own.
            if p.get('result') not in UNGRADED: continue
            key = str(p.get('source_key') or '')
            parts = key.split('|', 2)
            if len(parts) != 3:
                bad.append({'slate': slate, 'key': key, 'why': 'unparseable source_key'}); continue
            player, ptype, pline = parts
            hit = page('mlb_pipeline_props', 'id',
                       f'&game_date=eq.{slate}&player_name=eq.{requests.utils.quote(player)}'
                       f'&prop_type=eq.{ptype}&prop_line=eq.{pline}', cap=10)
            if hit: continue
            loose = page('mlb_pipeline_props', 'prop_line',
                         f'&game_date=eq.{slate}&player_name=eq.{requests.utils.quote(player)}'
                         f'&prop_type=eq.{ptype}', cap=50)
            bad.append({'slate': slate, 'key': key, 'result': p.get('result'),
                        'why': (f'line drift — props has {[l["prop_line"] for l in loose]}'
                                if loose else 'no row at any line')})
    if bad:
        rep.add('CRITICAL', 'DANGLING',
                f'MLB: {len(bad)} sweat_card picks reference a source row that does not exist', bad)


def check_orphandate(rep: Report, days: int, sport_filter: str | None):
    """Props filed on a game_date with no context rows for that sport."""
    cutoff = today_et()
    start = (datetime.fromisoformat(cutoff) - timedelta(days=days)).date().isoformat()
    for sport, (ctx, props) in SPORTS.items():
        if not props: continue
        if sport_filter and sport != sport_filter: continue
        p_dates = Counter(x['game_date'] for x in
                          page(props, 'game_date', f'&game_date=gte.{start}'))
        c_dates = {x['game_date'] for x in
                   page(ctx, 'game_date', f'&game_date=gte.{start}')}
        orphans = {d: n for d, n in p_dates.items() if d not in c_dates}
        if orphans:
            rep.add('CRITICAL', 'ORPHANDATE',
                    f'{sport}: {sum(orphans.values())} props on {len(orphans)} date(s) with NO '
                    f'game context — {dict(sorted(orphans.items()))}',
                    [{'game_date': d, 'props': n} for d, n in sorted(orphans.items())])


def check_dupctx(rep: Report, days: int, sport_filter: str | None):
    """One matchup holding more than one context row."""
    cutoff = today_et()
    start = (datetime.fromisoformat(cutoff) - timedelta(days=days)).date().isoformat()
    for sport, (ctx, _props) in SPORTS.items():
        if sport_filter and sport != sport_filter: continue
        rows = page(ctx, 'game_id,game_date,home_team,away_team,primary_play',
                    f'&game_date=gte.{start}')
        groups = defaultdict(list)
        for x in rows:
            groups[(x['game_date'], (x.get('home_team') or '').strip(),
                    (x.get('away_team') or '').strip())].append(x)
        dups = {k: v for k, v in groups.items() if len(v) > 1}
        if not dups: continue
        conflicting = []
        for k, v in dups.items():
            sides = set()
            for x in v:
                pp = x.get('primary_play') or {}
                if isinstance(pp, str):
                    try: pp = json.loads(pp)
                    except Exception: pp = {}
                if isinstance(pp, dict):
                    sides.add((pp.get('type'), pp.get('side'), pp.get('tier')))
            conflicting.append({'matchup': f'{k[2]} @ {k[1]}', 'date': k[0],
                                'rows': len(v), 'distinct_picks': len(sides),
                                'picks': sorted(str(s) for s in sides)})
        worst = [c for c in conflicting if c['distinct_picks'] > 1]
        sev = 'CRITICAL' if worst else 'WARN'
        rep.add(sev, 'DUPCTX',
                f'{sport}: {len(dups)} matchup(s) with duplicate context rows, '
                f'{len(worst)} of them disagree on the pick', conflicting)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--sport', help='limit to one sport')
    ap.add_argument('--quiet', action='store_true', help='summary only, no row detail')
    args = ap.parse_args()
    sport_filter = args.sport.upper() if args.sport else None

    print('=' * 72)
    print(f'RESOLUTION RECONCILIATION — {today_et()} (last {args.days}d)'
          f'{" · " + sport_filter if sport_filter else ""}')
    print('=' * 72)

    rep = Report()
    for fn in (check_stale, check_zeroval, check_dangling, check_orphandate, check_dupctx):
        try:
            fn(rep, args.days, sport_filter)
        except Exception as e:
            # A check that cannot run is itself a finding — never silent.
            rep.add('CRITICAL', 'CHECKFAIL',
                    f'{fn.__name__} could not complete: {type(e).__name__}: {e}')

    if not rep.findings:
        print('\n✅ clean — every pick on a past date has a result, '
              'no dangling refs, no orphan dates, no duplicate contexts.')
        return 0

    for sev, check, headline, rows in sorted(rep.findings, key=lambda f: f[0] != 'CRITICAL'):
        icon = '❌' if sev == 'CRITICAL' else '⚠️ '
        print(f'\n{icon} [{check}] {headline}')
        if args.quiet or not rows: continue
        for r in rows[:12]:
            print(f'      {json.dumps(r, default=str)[:190]}')
        if len(rows) > 12:
            print(f'      ... +{len(rows) - 12} more')

    crit = rep.critical
    print('\n' + '-' * 72)
    print(f'{crit} CRITICAL · {len(rep.findings) - crit} WARN')
    print('NOTHING WRITTEN — detector only.')
    return 1 if crit else 0


if __name__ == '__main__':
    raise SystemExit(main())
