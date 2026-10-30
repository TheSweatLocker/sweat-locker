#!/usr/bin/env python3
"""Guarantee a minimum number of published DOG side picks per slate.

== WHY (2026-10-29) ========================================================
Andy: "I think we need it to make it where we have some dawg favoites
essentially forcing the engine to picks at least 4 dawg spreads a week in
nfl, top dawg picks spread or ml based on the data and probablility of
upset"

THIS CORRECTS A CLAIM IN apply_chalk_cap.py. That file says "the engine does
not have dog opinions to promote." That was measured on NCAAF signals and it
is WRONG FOR NFL. The NFL ensemble forms a dog spread opinion on most games
and then THROWS IT AWAY:

    PHI @ WAS   _ensemble_all_markets.rl = PHI +6   LEAN conv 54
                published primary_play   = WAS -6   COVERAGE  edge -2.4
    LAC @ LA    rl = LAC +6.5 LEAN conv 54   published LA -6.5 COVERAGE -3.0
    BAL @ BUF   rl = BAL +3   LEAN conv 50   published BUF ML  COVERAGE -2.8
    LV  @ NYJ   rl = LV +1.5  LEAN conv 56   published NYJ ML  LEAN     +1.0

Each game publishes exactly ONE market as primary_play and the chooser takes
the highest conviction. Favourite ML conviction runs 83-88; spread conviction
runs 50-56. The chalk ML therefore wins every tie-break STRUCTURALLY, which
is the whole favourite bias in one line. So dog balance on NFL needs no
invented opinion -- it needs us to stop discarding the one we already have.

== HOW CANDIDATES ARE RANKED ==============================================
NOT by projection gap. Measured 2026-10-29 on 102 graded NFL games with both
a projection and a result, dog ATS cover by model-value bucket:

    dog value >= 3 pts   n=41   51.2%  CI[36.5,65.7]
    dog value 1-2 pts    n=14   35.7%
    dog value < 0        n=19   78.9%  CI[56.7,91.5]   <- BEST bucket

The gap is flat-to-inverted, so ranking by it would be worse than random.
(Consistent with projection running ~15pts light on big favourites.) The
blanket control is the honest headline: always-take-the-dog ATS is 57-45,
55.9% n=102, against a 52.38% breakeven at the -110 both sides a spread
carries -- NFL stores no spread price, which is why -110 is the bar here and
was the bar in the fade measurement too.

Ranking is therefore a waterfall, strongest evidence first:

  1. PUBLIC FADE, validated source. line_snapshot/oddscrowd rl bets_pct on
     the FAVOURITE >= --fade-bets. This is the only validated football
     spread edge we own: 63-44, 58.9%, CI[49.4,67.7], ROI +12.4%, n=107,
     same sign in both halves, monotonic in the threshold.
     See memory project_the_fade_is_the_one_real_edge_1019.
  2. PUBLIC FADE, cz/fr fallback, from <sport>_game_context.splits_summary.
     SAME RULE, UNVALIDATED SOURCE -- the n=107 was measured on OddsCrowd
     only, and cz money-flow has its own documented breakage (10/07). Off
     unless --allow-cz. Scored below every validated candidate.
  3. The ensemble's own dog rl opinion, by its stored conviction.
  4. A dog ML with a positive stored _model_edge_pp.

A candidate is VETOED when the public is heavy on the dog itself (fade
pointing the other way) unless the ensemble's own conviction clears
--veto-override-conv. That veto is what makes this a signal and not a quota:
LV +1.5 is the engine's highest-conviction dog this week (56) and 79% of
tickets are already on it, so it does not get published.

NEVER invents a line: the spread comes from the stored close_spread and the
label from the ensemble's own rl opinion where it has one. The prior play is
preserved at _pre_dog_floor so every promotion is auditable and reversible.

== THE PICK LOCK REFUSES THESE WRITES SILENTLY =============================
enforce_pick_lock() freezes side+type+team once pick_locked_at is stamped,
and a refused PATCH returns **204 with zero rows affected** -- identical to a
success. The first run of this script reported "promoted 4" and wrote
NOTHING; all four sat in pick_lock_drift as refusals. So:

  * every write is confirmed by READING THE ROW BACK, never by status code;
  * --unlock uses the sanctioned path (pick_locked_at = NULL, per
    20260929d), writes, then restores the original stamp, so the lock is
    re-armed and the audit trail in pick_lock_drift stays intact.

The real fix is ORDER: this step must run BEFORE the lock stamps, which is
how it is wired in the workflow. --unlock exists for a slate already frozen.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')
SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY'])
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json'}

ACTIONABLE = ('LEAN', 'STRONG', 'PRIME')
# NFL close_spread is a home MARGIN (nflverse); the others are betting lines.
CTX = {'NFL': ('nfl_game_context', True), 'NCAAF': ('ncaaf_game_context', False)}


def _pp(row):
    pp = row.get('primary_play') or {}
    if isinstance(pp, str):
        try:
            pp = json.loads(pp)
        except Exception:
            return None
    return pp if isinstance(pp, dict) else None


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _dog_side(close_spread, margin_convention):
    """Which side is the underdog. None when the game is a pick'em."""
    sp = _f(close_spread)
    if sp is None or abs(sp) < 1e-9:
        return None
    home_fav = (sp > 0) if margin_convention else (sp < 0)
    return 'AWAY' if home_fav else 'HOME'


def _dog_line(close_spread):
    """The dog's line as a positive number of points, from the STORED close."""
    sp = _f(close_spread)
    return None if sp is None else abs(sp)


def fetch_oddscrowd_rl(sport, game_ids, since_days=3):
    """Latest oddscrowd rl split per game. Paged -- an unpaged read silently
    caps at 1000 and would read as 'no coverage'."""
    if not game_ids:
        return {}
    since = (dt.date.today() - dt.timedelta(days=since_days)).isoformat()
    out, off = {}, 0
    while True:
        b = requests.get(f'{SB}/rest/v1/line_snapshot', headers=H_READ, params={
            'select': 'game_id,pick_side,bets_pct,money_pct,snapshot_ts,source',
            'sport': f'eq.{sport}', 'market': 'eq.rl',
            'snapshot_ts': f'gte.{since}',
            'order': 'snapshot_ts.desc', 'limit': 1000, 'offset': off},
            timeout=60).json()
        if not isinstance(b, list):
            return out
        for s in b:
            g = s.get('game_id')
            if g in game_ids and g not in out and s.get('bets_pct') is not None:
                out[g] = s
        if len(b) < 1000:
            break
        off += 1000
    return out


def write_play(tbl, game_id, new_pp, unlock=False):
    """Write primary_play and CONFIRM it by reading the row back.

    A PATCH refused by enforce_pick_lock returns 204 with zero rows affected,
    which is byte-identical to a success, so the status code cannot be
    trusted. Returns (ok, detail).
    """
    url = f'{SB}/rest/v1/{tbl}?game_id=eq.{game_id}'

    def _put(payload):
        return requests.patch(url, headers=H_WRITE, json=payload, timeout=20)

    def _read():
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ, params={
            'select': 'primary_play,pick_locked_at',
            'game_id': f'eq.{game_id}'}, timeout=20).json()
        return r[0] if isinstance(r, list) and r else {}

    want = (str(new_pp.get('side')), str(new_pp.get('type')),
            str(new_pp.get('tier')))

    def _landed():
        pp = _read().get('primary_play') or {}
        return (str(pp.get('side')), str(pp.get('type')),
                str(pp.get('tier'))) == want

    resp = _put({'primary_play': new_pp})
    if resp.status_code not in (200, 204):
        return False, f'patch {resp.status_code}: {resp.text[:120]}'
    if _landed():
        return True, 'written'

    # Refused. The lock is the only thing that does this silently.
    stamp = _read().get('pick_locked_at')
    if not unlock:
        return False, (f'REFUSED by the pick lock (locked {str(stamp)[:19]}) - '
                       f'204 but zero rows changed; re-run with --unlock')
    u = _put({'pick_locked_at': None})
    if u.status_code not in (200, 204):
        return False, f'unlock failed {u.status_code}: {u.text[:120]}'
    resp = _put({'primary_play': new_pp})
    ok = resp.status_code in (200, 204) and _landed()
    # Re-arm the lock on the new pick whatever happened, so a failed write
    # never leaves the slate unlocked.
    if stamp:
        _put({'pick_locked_at': stamp})
    if not ok:
        return False, 'still refused after unlock - not a lock problem'
    return True, f'written via --unlock (lock re-armed at {str(stamp)[:19]})'


def _cz_rl_bets(splits):
    """(away_bets_pct, home_bets_pct) from splits_summary.rl, or (None, None)."""
    if not isinstance(splits, dict):
        return (None, None)
    rl = splits.get('rl')
    if not isinstance(rl, dict):
        return (None, None)

    def g(k):
        d = rl.get(k)
        return _f(d.get('bets_pct_avg')) if isinstance(d, dict) else None
    return (g('AWAY'), g('HOME'))


def build_candidates(rows, margin, fade_bets, allow_cz, oc, veto_override):
    """One best dog candidate per game, with full provenance."""
    cands = []
    for r in rows:
        pp = _pp(r)
        if pp is None:
            continue
        dog = _dog_side(r.get('close_spread'), margin)
        line = _dog_line(r.get('close_spread'))
        if dog is None or line is None:
            continue

        # Already publishing this dog at an actionable tier? Nothing to do.
        if (str(pp.get('side') or '') == dog
                and str(pp.get('tier')) in ACTIONABLE
                and str(pp.get('type')) in ('rl', 'spread')):
            cands.append({'row': r, 'pp': pp, 'dog': dog, 'line': line,
                          'already': True, 'score': 0.0,
                          'why': ['already published'],
                          'conv': _f(pp.get('conviction')) or 0,
                          'label': pp.get('label'), 'src': 'existing'})
            continue

        am = pp.get('_ensemble_all_markets') or {}
        rl_op = am.get('rl') if isinstance(am, dict) else None
        rl_op = rl_op if isinstance(rl_op, dict) else {}
        ml_op = am.get('ml') if isinstance(am, dict) else None
        ml_op = ml_op if isinstance(ml_op, dict) else {}

        ens_dog = str(rl_op.get('pick') or '').startswith(dog)
        ens_conv = _f(rl_op.get('conviction')) or 0.0
        ens_label = rl_op.get('label') if ens_dog else None

        # ---- public money, on the FAVOURITE (what the fade needs) --------
        fav = 'HOME' if dog == 'AWAY' else 'AWAY'
        oc_row = oc.get(r['game_id'])
        oc_fav_bets = oc_dog_bets = None
        if oc_row:
            b = _f(oc_row.get('bets_pct'))
            if b is not None:
                if str(oc_row.get('pick_side')) == fav:
                    oc_fav_bets = b
                elif str(oc_row.get('pick_side')) == dog:
                    oc_dog_bets = b
        a_b, h_b = _cz_rl_bets(r.get('splits_summary'))
        cz_fav_bets = h_b if fav == 'HOME' else a_b
        cz_dog_bets = h_b if dog == 'HOME' else a_b

        why, score, src = [], 0.0, None
        # 1 - validated fade
        if oc_fav_bets is not None and oc_fav_bets >= fade_bets:
            score += 100 + (oc_fav_bets - fade_bets)
            why.append(f'fade public {oc_fav_bets:.0f}% on the favourite '
                       f'(oddscrowd, VALIDATED 58.9% n=107 +12.4%)')
            src = 'fade_oddscrowd'
        # 2 - cz/fr fade, same rule, unvalidated source
        elif allow_cz and cz_fav_bets is not None and cz_fav_bets >= fade_bets:
            score += 50 + (cz_fav_bets - fade_bets)
            why.append(f'fade public {cz_fav_bets:.0f}% on the favourite '
                       f'(cz/fr - SAME RULE, UNVALIDATED SOURCE)')
            src = 'fade_czfr'
        # 3 - the ensemble's own dog opinion
        if ens_dog:
            score += ens_conv
            why.append(f"ensemble rl wants this dog: {rl_op.get('label')} "
                       f"{rl_op.get('tier')} conv {ens_conv:.0f}")
            src = src or 'ensemble_rl'
        # 4 - a dog ML with a positive measured edge
        edge = _f(pp.get('_model_edge_pp'))
        if (str(ml_op.get('pick') or '').startswith(dog)
                and edge is not None and edge > 0):
            score += edge
            why.append(f'dog ML carries a positive measured edge +{edge:.1f}pp')
            src = src or 'dog_ml_edge'

        if src is None:
            continue

        dog_bets = oc_dog_bets if oc_dog_bets is not None else cz_dog_bets
        # ---- sub-threshold public tilt, as a TIE-BREAK ONLY -------------
        # The fade above is a threshold rule because that is how it was
        # measured. But between two ensemble dogs of equal conviction the
        # fade direction still carries information, so lean is worth a few
        # points -- scaled so it can separate a conv-51 from a conv-50 and
        # can never outrank a validated fade (which scores 100+).
        _fb = oc_fav_bets if oc_fav_bets is not None else cz_fav_bets
        if _fb is not None and dog_bets is not None:
            tilt = (_fb - dog_bets) / 10.0
            if abs(tilt) >= 0.5:
                score += tilt
                why.append(f'public lean tie-break: {_fb:.0f}% favourite / '
                           f'{dog_bets:.0f}% dog ({tilt:+.1f})')

        # ---- the veto that keeps this a signal, not a quota --------------
        veto = None
        if dog_bets is not None and dog_bets >= fade_bets and ens_conv < veto_override:
            veto = (f'public is {dog_bets:.0f}% ON THE DOG - the fade points the '
                    f'other way and ensemble conv {ens_conv:.0f} < {veto_override:.0f}')

        cands.append({'row': r, 'pp': pp, 'dog': dog, 'line': line,
                      'already': False, 'score': score, 'why': why, 'veto': veto,
                      'conv': ens_conv, 'label': ens_label, 'src': src,
                      'fav_bets': oc_fav_bets if oc_fav_bets is not None else cz_fav_bets,
                      'dog_bets': dog_bets})
    return cands


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(CTX))
    ap.add_argument('--date', help='single game_date; default = all from today on')
    ap.add_argument('--until', help='end game_date (inclusive) when --date is not set')
    ap.add_argument('--min-dogs', type=int, default=4,
                    help='minimum published dog side picks per slate')
    ap.add_argument('--fade-bets', type=float, default=65.0,
                    help='public bets%% on the favourite that triggers the fade')
    ap.add_argument('--veto-override-conv', type=float, default=70.0,
                    help='ensemble conviction that survives a public-on-the-dog veto')
    ap.add_argument('--promote-tier', default='LEAN', choices=['LEAN', 'STRONG'])
    ap.add_argument('--allow-cz', action='store_true',
                    help='permit the cz/fr split fallback (unvalidated source)')
    ap.add_argument('--unlock', action='store_true',
                    help='clear pick_locked_at to write, then re-arm the lock; '
                         'needed only on a slate already frozen')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    tbl, margin = CTX[a.sport]
    sel = ('game_id,game_date,home_team,away_team,close_spread,'
           'primary_play,splits_summary')
    parts = [f'select={sel}', 'order=game_date.asc', 'limit=1000']
    if a.date:
        parts.append(f'game_date=eq.{a.date}')
    else:
        parts.append('game_date=gte.' + dt.date.today().isoformat())
        if a.until:
            parts.append(f'game_date=lte.{a.until}')
    rows = requests.get(f'{SB}/rest/v1/{tbl}?' + '&'.join(parts),
                        headers=H_READ, timeout=60).json()
    if not isinstance(rows, list):
        print(f'  ! read failed: {str(rows)[:200]}')
        return 2

    oc = fetch_oddscrowd_rl(a.sport, {r['game_id'] for r in rows})
    print(f'=== apply_dog_floor · {a.sport} · floor {a.min_dogs} dogs/slate · '
          f'fade>={a.fade_bets:.0f}%{"" if a.apply else " [DRY RUN]"} ===')
    print(f'  {len(rows)} games read · oddscrowd rl coverage {len(oc)}/{len(rows)}'
          + ('' if oc else '  <- VALIDATED SOURCE IS DARK on this slate'))

    bydate = collections.defaultdict(list)
    for c in build_candidates(rows, margin, a.fade_bets, a.allow_cz, oc,
                              a.veto_override_conv):
        bydate[c['row']['game_date']].append(c)

    promoted = failed = 0
    for d in sorted(bydate):
        cs = bydate[d]
        have = [c for c in cs if c['already']]
        pool = sorted([c for c in cs if not c['already'] and not c.get('veto')],
                      key=lambda c: (-c['score'], str(c['row']['game_id'])))
        vetoed = [c for c in cs if not c['already'] and c.get('veto')]
        need = max(0, a.min_dogs - len(have))
        print(f'\n  --- {d} · {len(have)} dog pick(s) already published · '
              f'need {need} more · {len(pool)} candidate(s), {len(vetoed)} vetoed ---')
        for c in vetoed:
            r = c['row']
            print(f"      VETO  {r['away_team']}@{r['home_team']} "
                  f"{c['dog']} +{c['line']:g}: {c['veto']}")
        if not need:
            continue
        if len(pool) < need:
            print(f'      ! only {len(pool)} candidate(s) for a floor of {a.min_dogs}'
                  f' - publishing {len(pool)}, NOT inventing the rest')
        for c in pool[:need]:
            r, pp = c['row'], c['pp']
            team = r['home_team'] if c['dog'] == 'HOME' else r['away_team']
            label = c['label'] or f"{team} +{c['line']:g}"
            print(f"      PROMOTE {label:24s} score {c['score']:6.1f}  [{c['src']}]")
            for w in c['why']:
                print(f'                 · {w}')
            if not a.apply:
                continue
            new = dict(pp)
            new['_pre_dog_floor'] = {'type': pp.get('type'), 'side': pp.get('side'),
                                     'tier': pp.get('tier'), 'label': pp.get('label'),
                                     'conviction': pp.get('conviction')}
            new['_dog_floor'] = {'reason': c['why'], 'source': c['src'],
                                 'score': round(c['score'], 2),
                                 'fav_bets_pct': c.get('fav_bets'),
                                 'floor': a.min_dogs,
                                 'applied_at': dt.datetime.now(dt.timezone.utc).isoformat()}
            new['type'] = 'rl'
            new['side'] = c['dog']
            new['line'] = c['line']
            new['label'] = label
            new['tier'] = a.promote_tier
            if c['conv']:
                new['conviction'] = int(c['conv'])
            ok, detail = write_play(tbl, r['game_id'], new, unlock=a.unlock)
            if ok:
                promoted += 1
                print(f'                 -> {detail}')
            else:
                failed += 1
                print(f'                 ! NOT WRITTEN: {detail}')
    print()
    if a.apply:
        print(f'  promoted {promoted} dog pick(s)'
              + (f' · {failed} REFUSED' if failed else ''))
        if failed:
            return 1
    else:
        print('  DRY RUN - nothing written')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
