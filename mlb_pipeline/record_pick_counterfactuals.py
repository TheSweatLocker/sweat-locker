"""Record and grade the pick we did NOT publish.

Andy 2026-10-08: "Lets log which matchups to saee if the flipp would have been
correct additonally find out why it triedng to flip"

Two sources of disagreement, both real, both previously unmeasurable:

  manual      a human directed a different pick than the engine computed.
              Today: CLE @ CHW. lr_v1 had Cleveland (p_home 0.274); we
              published Chicago on the resolver + jerry_pred + the dawg cohort
              + 2 of 3 externals. The old state survives in
              primary_play._pre_override, which is what this reads.

  lock_drift  `recompute_nfl_primary_play` re-scores once team form, defense
              and team stats land, and the DB pick lock refuses the write. On
              2026-10-08 it wanted to change 24 of 31 NFL games including 9
              side flips. Every refusal is already in `pick_lock_drift`; the
              side we refused has never been graded.

Why it matters: project_jerry_override_costs_the_edge_1007 measured the
override PATH at 49% against the model's 58%, in aggregate. This measures it
per decision, so it can eventually move a gate instead of staying a note.

GRADING
  ml      home_win
  rl      spread_result ('home_covered'/'away_covered'/'push')
Both sides of one game are graded from the same result row, so they cannot
disagree about what happened. A side we cannot grade is left NULL and the
verdict stays 'undecided' -- never guessed.

Units use each side's own price, because a flip usually changes the price and
a units verdict that ignores that is worthless. Missing price -> -110.

CLI
    python record_pick_counterfactuals.py --days 10
    python record_pick_counterfactuals.py --days 10 --apply
    python record_pick_counterfactuals.py --grade --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from datetime import date, timedelta
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
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'MLB': 'mlb_game_context', 'NHL': 'nhl_game_context'}
RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'MLB': 'mlb_game_results', 'NHL': 'nhl_game_results'}

#: "locked pick changed: side HOME->AWAY type rl->ml team UTSA->South Florida"
_DRIFT = re.compile(
    r'side\s+(?P<os>\w+)->(?P<ns>\w+)\s+type\s+(?P<ot>\S+)->(?P<nt>\S+)\s+'
    r'team\s+(?P<oteam>.+?)->(?P<nteam>.+?)$')


def _jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _get(table, params):
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=params,
                     timeout=180)
    if r.status_code == 404:
        raise SystemExit(
            f'\n  {table} does not exist. Apply '
            f'supabase/migrations/20261008a_pick_counterfactual.sql first.')
    if r.status_code not in (200, 206):
        print(f'  ! {table} {r.status_code} {r.text[:150]}')
        return []
    j = r.json()
    return j if isinstance(j, list) else []


def _units(result, odds):
    """Units won/lost on a 1u stake at American odds."""
    if result not in ('WIN', 'LOSS', 'PUSH'):
        return None
    if result == 'PUSH':
        return 0.0
    try:
        o = int(odds) if odds is not None else -110
    except (TypeError, ValueError):
        o = -110
    if result == 'LOSS':
        return -1.0
    return round(o / 100.0, 3) if o > 0 else round(100.0 / abs(o), 3)


def _grade_side(sport, res, side, market):
    """WIN/LOSS/PUSH for one side of one game, or None."""
    if not res or side not in ('HOME', 'AWAY'):
        return None
    m = str(market or '').lower()
    if m == 'ml':
        hw = res.get('home_win')
        if hw is None:
            return None
        return ('WIN' if bool(hw) else 'LOSS') if side == 'HOME' \
            else ('LOSS' if bool(hw) else 'WIN')
    if m in ('rl', 'spread'):
        sr = str(res.get('spread_result') or '').lower()
        if sr in ('push',):
            return 'PUSH'
        if sr == 'home_covered':
            return 'WIN' if side == 'HOME' else 'LOSS'
        if sr == 'away_covered':
            return 'WIN' if side == 'AWAY' else 'LOSS'
    return None


def record(sports, days, apply):
    today = date.today()
    lo = (today - timedelta(days=days)).isoformat()
    hi = (today + timedelta(days=10)).isoformat()
    rows_out = []

    # ── 1. manual overrides: primary_play._pre_override ──────────────────
    for sport in sports:
        tbl = CTX.get(sport)
        if not tbl:
            continue
        for g in _get(tbl, {'select': 'game_id,game_date,home_team,away_team,'
                                      'primary_play',
                            'and': f'(game_date.gte.{lo},game_date.lte.{hi})',
                            'limit': '400'}):
            pp = _jl(g.get('primary_play'))
            pre = pp.get('_pre_override')
            if not isinstance(pre, dict) or not pre.get('side'):
                continue
            if pre.get('side') == pp.get('side') and \
                    pre.get('label') == pp.get('label'):
                continue
            rows_out.append({
                'sport': sport, 'game_date': str(g['game_date'])[:10],
                'game_id': str(g['game_id']),
                'matchup': f"{g['away_team']} @ {g['home_team']}",
                'origin': 'manual',
                'reason': str(pp.get('audit_note') or '')[:400],
                'published_side': pp.get('side'),
                'published_label': pp.get('label'),
                'published_market': pp.get('type'),
                'published_tier': pp.get('tier'),
                'published_conv': pp.get('conviction'),
                'alt_side': pre.get('side'), 'alt_label': pre.get('label'),
                'alt_market': pre.get('type'), 'alt_tier': pre.get('tier'),
                'alt_conv': pre.get('conviction'),
            })

    # ── 2. lock_drift: refusals the trigger logged ───────────────────────
    drift = _get('pick_lock_drift',
                 {'select': 'game_id,reason,attempted_at',
                  'attempted_at': f'gte.{lo}', 'limit': '1000'})
    ctxcache = {}
    for sport in sports:
        tbl = CTX.get(sport)
        if not tbl:
            continue
        for g in _get(tbl, {'select': 'game_id,game_date,home_team,away_team,'
                                      'primary_play',
                            'and': f'(game_date.gte.{lo},game_date.lte.{hi})',
                            'limit': '400'}):
            ctxcache[str(g['game_id'])] = (sport, g)

    seen = set()
    for d in drift:
        m = _DRIFT.search(str(d.get('reason') or ''))
        if not m:
            continue                      # e.g. "game already started"
        gid = str(d.get('game_id'))
        hit = ctxcache.get(gid)
        if not hit:
            continue
        sport, g = hit
        alt_label = m.group('nteam').strip()
        key = (gid, alt_label)
        if key in seen:
            continue                      # same refusal every night
        seen.add(key)
        pp = _jl(g.get('primary_play'))
        rows_out.append({
            'sport': sport, 'game_date': str(g['game_date'])[:10],
            'game_id': gid,
            'matchup': f"{g['away_team']} @ {g['home_team']}",
            'origin': 'lock_drift',
            'reason': f"pick lock refused: {str(d.get('reason'))[:300]}",
            'published_side': pp.get('side'),
            'published_label': pp.get('label'),
            'published_market': pp.get('type'),
            'published_tier': pp.get('tier'),
            'published_conv': pp.get('conviction'),
            'alt_side': m.group('ns'), 'alt_label': alt_label,
            'alt_market': m.group('nt'),
        })

    print(f'=== record · {len(rows_out)} counterfactual(s) '
          f'· {"APPLY" if apply else "DRY"} ===')
    for r in sorted(rows_out, key=lambda z: (z['game_date'], z['matchup'])):
        print(f"  {r['game_date']}  {r['sport']:<6}{r['matchup'][:30]:<32}"
              f"{r['origin']:<11}published {str(r['published_label'])[:20]:<22}"
              f"alt {str(r['alt_label'])[:20]}")
    if apply and rows_out:
        # PostgREST rejects a batch whose objects have differing key sets
        # ("All object keys must match", PGRST102). The manual rows carry
        # tier/conviction that the lock_drift rows cannot know, so union the
        # keys and fill the gaps with None rather than sending ragged dicts.
        allkeys = sorted({k for row in rows_out for k in row})
        rows_out = [{k: row.get(k) for k in allkeys} for row in rows_out]

        # Dedupe in Python rather than via ON CONFLICT. The unique index is on
        # (game_id, origin, COALESCE(alt_label,'')) and PostgREST cannot match
        # an EXPRESSION index in on_conflict (42P10). Reading the existing keys
        # first is also honest about what it skips.
        have = {(str(x.get('game_id')), str(x.get('origin')),
                 str(x.get('alt_label') or ''))
                for x in _get('pick_counterfactual',
                              {'select': 'game_id,origin,alt_label',
                               'limit': '2000'})}
        fresh = [r for r in rows_out
                 if (str(r.get('game_id')), str(r.get('origin')),
                     str(r.get('alt_label') or '')) not in have]
        print(f'  {len(rows_out) - len(fresh)} already recorded · '
              f'{len(fresh)} new')
        if not fresh:
            return len(rows_out)
        r = requests.post(f'{SB}/rest/v1/pick_counterfactual', headers=H_W,
                          data=json.dumps(fresh), timeout=120)
        if r.status_code not in (200, 201, 204):
            print(f'  ! insert {r.status_code} {r.text[:300]}')
        else:
            print(f'  inserted {len(r.json()) if r.content else len(fresh)}')
    return len(rows_out)


def grade(apply):
    pend = _get('pick_counterfactual',
                {'select': '*', 'graded_at': 'is.null', 'limit': '500'})
    print(f'=== grade · {len(pend)} ungraded ===')
    done = 0
    for row in pend:
        sport = str(row['sport'])
        rtbl = RESULTS.get(sport)
        if not rtbl:
            continue
        res = _get(rtbl, {'select': 'game_id,home_win,spread_result,'
                                    'home_score,away_score',
                          'game_id': f"eq.{row['game_id']}"})
        res = res[0] if res else None
        if not res or res.get('home_score') is None:
            continue
        pr = _grade_side(sport, res, row.get('published_side'),
                         row.get('published_market'))
        ar = _grade_side(sport, res, row.get('alt_side'),
                         row.get('alt_market'))
        if pr is None or ar is None:
            print(f"  {row['matchup']}: cannot grade "
                  f"(published={pr} alt={ar}) — left undecided")
            continue
        pu = _units(pr, row.get('published_odds'))
        au = _units(ar, row.get('alt_odds'))
        if pu is None or au is None:
            verdict = 'undecided'
        elif abs(pu - au) < 1e-9:
            verdict = 'same'
        else:
            verdict = 'published_better' if pu > au else 'alt_better'
        print(f"  {row['game_date']} {row['matchup'][:28]:<30}"
              f"{row['origin']:<11}"
              f"published {row['published_label']} {pr} ({pu:+.3f}u)  "
              f"vs alt {row['alt_label']} {ar} ({au:+.3f}u)  -> {verdict}")
        done += 1
        if not apply:
            continue
        requests.patch(f"{SB}/rest/v1/pick_counterfactual?id=eq.{row['id']}",
                       headers=H_W,
                       data=json.dumps({
                           'published_result': pr, 'alt_result': ar,
                           'published_units': pu, 'alt_units': au,
                           'verdict': verdict,
                           'graded_at': dt.datetime.now(
                               dt.timezone.utc).isoformat()}), timeout=60)
    print(f'  {"graded" if apply else "gradeable"} {done}')

    # standing scoreboard
    allrows = _get('pick_counterfactual',
                   {'select': 'origin,verdict,published_units,alt_units',
                    'verdict': 'not.is.null', 'limit': '1000'})
    if allrows:
        print('\n  SCOREBOARD — did the thing we published beat the thing we '
              'discarded?')
        for origin in sorted({str(r['origin']) for r in allrows}):
            sub = [r for r in allrows if str(r['origin']) == origin]
            pb = sum(1 for r in sub if r['verdict'] == 'published_better')
            ab = sum(1 for r in sub if r['verdict'] == 'alt_better')
            pu = sum(float(r['published_units'] or 0) for r in sub)
            au = sum(float(r['alt_units'] or 0) for r in sub)
            print(f'    {origin:<12}n={len(sub):<4}published better {pb} · '
                  f'alt better {ab} · units {pu:+.2f} vs {au:+.2f}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=10)
    ap.add_argument('--sport', default=None)
    ap.add_argument('--grade', action='store_true')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    sports = [a.sport.upper()] if a.sport else ['NFL', 'NCAAF', 'MLB', 'NHL']
    if a.grade:
        grade(a.apply)
    else:
        record(sports, a.days, a.apply)
        grade(a.apply)
    return 0


if __name__ == '__main__':
    sys.exit(main())
