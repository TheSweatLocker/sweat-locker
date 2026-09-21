"""Shadow-record what the NCAAF spread model WOULD have picked, and grade
it against what we actually published.

WHY THIS EXISTS
Walk-forward on 3 weeks (2026-08-28..09-20) found the published pick
losing to the engine's own projected_spread out of sample:

    follow projected_spread   TRAIN 55.3% (n=76)   TEST 60.0% (n=45)
    published pick as-is      TRAIN 61.9% (n=105)  TEST 46.8% (n=47)

and on the 66 games carrying both, the published side DISAGREED with the
model's own edge on 41 (62%) -- going 18-23 where the model side went
23-18. All 41 disagreements came from ensemble_v2.

n=66 is roughly five games of difference. That is far too thin to flip
the engine on, and exactly the situation where you record evidence and
wait instead of acting. Same discipline as the NFL prop PRIME cap: the
gate lifts on evidence, never on a hunch or a date.

WHY STAMP RATHER THAN DERIVE
The model side is recomputable later from stored columns -- nothing is
destroyed. What is NOT recoverable is the line we actually had AT PICK
TIME: `close_spread` drifts to the closing number, so a retroactive
calculation grades the model against a line it never saw. The stamp is
write-once per game precisely to freeze that.

Never rendered. Lives in primary_play._model_edge_shadow.

CLI
  python ncaaf_model_edge_shadow.py                  # stamp today
  python ncaaf_model_edge_shadow.py --days 7
  python ncaaf_model_edge_shadow.py --dry-run
  python ncaaf_model_edge_shadow.py --report --days 40
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

for _p in ('mlb_pipeline/.env', '.env'):
    if os.path.exists(_p):
        load_dotenv(_p)
        break

SB = os.environ.get('SUPABASE_URL')
K = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY'))
if not (SB and K):
    sys.exit('SUPABASE_URL / SUPABASE_KEY not set')
H_R = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

SHADOW_KEY = '_model_edge_shadow'
MIN_EDGE = 0.5          # below this the model has no opinion

# Only stamp games kicking off within this many days.
#
# "As of decision time" has to mean something precise. NCAAF picks freeze
# at the Thursday 08:00 ET lock, so the stamp belongs at that lock, not
# before it: stamping a Saturday game on the preceding Sunday would
# freeze a line for a pick that still gets regenerated Mon/Tue/Wed, and
# write-once means that premature stamp is the one that sticks.
#
# A 4-day horizon run on the Thursday lock cron covers Thu->Sun (the
# whole college slate) and naturally excludes next week's games, which
# are still in their open write window.
HORIZON_DAYS = 4


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dates(days: int, date: str | None) -> list[str]:
    if date:
        return [date]
    base = datetime.strptime(_today_et(), '%Y-%m-%d')
    return [(base - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(max(days, 1))]


def _fetch(lo: str, hi: str, select: str) -> list[dict]:
    out, off = [], 0
    while off < 40000:
        p = [('select', select), ('game_date', f'gte.{lo}'), ('game_date', f'lte.{hi}'),
             ('limit', '1000'), ('offset', str(off))]
        r = requests.get(f'{SB}/rest/v1/ncaaf_game_context', params=p, headers=H_R, timeout=60)
        if r.status_code != 200:
            print(f'  ⚠ ctx fetch HTTP {r.status_code}: {r.text[:160]}')
            return out
        b = r.json()
        if not b:
            break
        out += b
        off += 1000
        if len(b) < 1000:
            break
    return out


def model_edge(projected_spread, close_spread):
    """-> (side, edge) or (None, None).

    NCAAF sign conventions are OPPOSITE between these two columns:
    projected_spread POSITIVE = home favored, close_spread NEGATIVE =
    home favored. So differencing them means ADDING. Getting this wrong
    named the wrong team in 79% of games in the casual summary (fixed
    2026-09-20, 9e595117) -- do not "simplify" this to a subtraction.
    """
    p, c = _f(projected_spread), _f(close_spread)
    if p is None or c is None:
        return None, None
    edge = p + c
    if abs(edge) < MIN_EDGE:
        return None, edge
    return ('HOME' if edge > 0 else 'AWAY'), edge


def stamp(days: int, date: str | None, dry_run: bool,
          horizon: int = HORIZON_DAYS) -> int:
    ds = _dates(days, date)
    lo, hi = min(ds), max(ds)
    rows = _fetch(lo, hi, 'game_id,game_date,home_team,away_team,primary_play,'
                          'projected_spread,close_spread,kickoff_utc')
    print(f'=== ncaaf_model_edge_shadow · {lo}..{hi} · {len(rows)} games '
          f'{"(DRY)" if dry_run else ""} ===')
    stamped = already = no_model = no_pick = failed = kicked = too_early = 0
    agree = disagree = 0
    now = datetime.now(timezone.utc)
    for g in rows:
        # NEVER stamp a game that has already kicked off. The stamp's only
        # purpose is freezing the line we held AT DECISION TIME; writing it
        # after the fact records the drifted/closing line under an
        # "as-of" label and manufactures evidence that was never true.
        # A shadow you backfilled is not a shadow, it is a retrofit.
        ko = g.get('kickoff_utc')
        if ko:
            try:
                kdt = datetime.fromisoformat(str(ko).replace('Z', '+00:00'))
                if kdt.tzinfo is None:
                    kdt = kdt.replace(tzinfo=timezone.utc)
                if kdt <= now:
                    kicked += 1
                    continue
                if kdt > now + timedelta(days=horizon):
                    # Too far out: this pick is still inside its open
                    # write window and will be rewritten before kickoff.
                    too_early += 1
                    continue
            except (TypeError, ValueError):
                pass
        else:
            # No kickoff time: fall back to the date. Anything before today
            # is certainly done.
            if str(g.get('game_date') or '') < _today_et():
                kicked += 1
                continue
        pp = g.get('primary_play')
        if not isinstance(pp, dict) or not pp:
            no_pick += 1
            continue
        if isinstance(pp.get(SHADOW_KEY), dict):
            # WRITE-ONCE. The whole point is freezing the line we had at
            # decision time; re-stamping against a drifted line destroys
            # exactly the evidence this script exists to preserve.
            already += 1
            continue
        side, edge = model_edge(g.get('projected_spread'), g.get('close_spread'))
        if side is None:
            no_model += 1
            continue
        pub = str(pp.get('side') or '').upper()
        mkt = str(pp.get('type') or '').lower()
        if mkt in ('rl', 'spread') and pub in ('HOME', 'AWAY'):
            if pub == side:
                agree += 1
            else:
                disagree += 1
        shadow = {
            'side': side,
            'edge': round(edge, 2),
            'projected_spread': _f(g.get('projected_spread')),
            'close_spread_at_stamp': _f(g.get('close_spread')),
            'published_side': pub or None,
            'published_market': mkt or None,
            'published_tier': pp.get('tier'),
            'stamped_at': datetime.now(timezone.utc).isoformat(),
        }
        if dry_run:
            stamped += 1
            continue
        new_pp = {**pp, SHADOW_KEY: shadow}
        r = requests.patch(f'{SB}/rest/v1/ncaaf_game_context',
                           params={'game_id': f'eq.{g["game_id"]}'},
                           headers=H_W, json={'primary_play': new_pp}, timeout=20)
        if r.status_code in (200, 204):
            stamped += 1
        else:
            failed += 1
            if failed <= 3:
                print(f'  ⚠ patch {g["game_id"]}: HTTP {r.status_code} {r.text[:120]}')
    print(f'  stamped={stamped} already={already} already_kicked={kicked} '
          f'beyond_{horizon}d_horizon={too_early} no_model={no_model} '
          f'no_pick={no_pick} failed={failed}')
    if agree or disagree:
        tot = agree + disagree
        print(f'  of newly stamped spread picks: published agrees with model '
              f'{agree}/{tot} ({100.0*agree/tot:.0f}%), disagrees {disagree}/{tot}')
    return stamped


def report(days: int) -> None:
    """Grade shadow vs published on games that have finished."""
    ds = _dates(days, None)
    lo, hi = min(ds), max(ds)
    ctx = _fetch(lo, hi, 'game_id,game_date,home_team,away_team,primary_play,'
                         'projected_spread,close_spread')
    res, off = {}, 0
    while off < 40000:
        p = [('select', 'game_id,home_score,away_score,close_spread'),
             ('game_date', f'gte.{lo}'), ('game_date', f'lte.{hi}'),
             ('limit', '1000'), ('offset', str(off))]
        r = requests.get(f'{SB}/rest/v1/ncaaf_game_results', params=p, headers=H_R, timeout=60)
        if r.status_code != 200:
            break
        b = r.json()
        if not b:
            break
        for x in b:
            if x.get('home_score') is not None:
                res[x['game_id']] = x
        off += 1000
        if len(b) < 1000:
            break

    def cover(margin, cs):
        d = margin + cs
        return 'PUSH' if abs(d) < 1e-9 else ('HOME' if d > 0 else 'AWAY')

    pub = [0, 0, 0]
    mod = [0, 0, 0]
    dis_pub = [0, 0]
    dis_mod = [0, 0]
    live = 0
    for g in ctx:
        r = res.get(g['game_id'])
        pp = g.get('primary_play')
        if not r or not isinstance(pp, dict):
            continue
        cs = _f(r.get('close_spread')) or _f(g.get('close_spread'))
        if cs is None:
            continue
        sh = pp.get(SHADOW_KEY)
        if isinstance(sh, dict):
            mside = sh.get('side')
            live += 1
        else:
            mside, _ = model_edge(g.get('projected_spread'), g.get('close_spread'))
        pside = str(pp.get('side') or '').upper()
        if str(pp.get('type') or '').lower() not in ('rl', 'spread'):
            continue
        if pside not in ('HOME', 'AWAY') or mside not in ('HOME', 'AWAY'):
            continue
        got = cover(r['home_score'] - r['away_score'], cs)
        if got == 'PUSH':
            pub[2] += 1
            mod[2] += 1
            continue
        pub[0 if got == pside else 1] += 1
        mod[0 if got == mside else 1] += 1
        if pside != mside:
            dis_pub[0 if got == pside else 1] += 1
            dis_mod[0 if got == mside else 1] += 1

    def show(lbl, v):
        w, l = v[0], v[1]
        n = w + l
        if not n:
            print(f'  {lbl:<34} no graded games')
            return
        tag = '' if n >= 30 else '  ! n<30, not actionable'
        print(f'  {lbl:<34} {w}-{l}  n={n:<4} {100.0*w/n:5.1f}%  '
              f'{w*0.909-l:+7.2f}u{tag}')

    print(f'=== shadow report · {lo}..{hi} ===')
    print(f'  games graded with a stamped (live) shadow: {live}')
    print('  (unstamped games fall back to recomputing from stored columns,')
    print('   which grades against the CLOSING line, not the line we had)\n')
    show('published pick', pub)
    show('model edge would have been', mod)
    print()
    show('  where they disagree: published', dis_pub)
    show('  where they disagree: model', dis_mod)
    n = dis_pub[0] + dis_pub[1]
    print(f'\n  VERDICT: {"enough sample to act (n>=100 disagreements)" if n >= 100 else f"keep accumulating — {n}/100 disagreements graded"}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=3)
    ap.add_argument('--date')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--horizon', type=int, default=HORIZON_DAYS,
                    help='only stamp games kicking off within N days')
    a = ap.parse_args()
    if a.report:
        report(a.days if a.days > 3 else 40)
    else:
        stamp(a.days, a.date, a.dry_run, a.horizon)


if __name__ == '__main__':
    main()
