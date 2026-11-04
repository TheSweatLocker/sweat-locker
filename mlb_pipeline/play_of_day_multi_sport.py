"""Cross-sport POTD selector — runs AFTER play_of_day.py.

play_of_day.py is MLB-only (4863 lines, 11 mlb_game_context refs, 0 other-
sport refs). Rather than refactor that file pre-launch, this companion
module scans NFL/NCAAF/NBA/NCAAB/NHL game_contexts for the strongest
PRIME/STRONG pick, applies the LR predictor as a gate, and — if the
cross-sport winner has meaningfully higher conviction than the MLB POTD
already published to jerry_cache.best_bet_{today} — OVERRIDES with the
football/hoops/hockey pick. Otherwise leaves MLB POTD alone.

LR PROBABILITY FOLD (matches Dawg-of-Day pattern shipped 9/3):
  Each candidate gets p_pick_wins evaluated via defensive_gates._lr_predict_ml.
  Candidates where LR sees COIN or FADE (p < 0.55) get their conviction
  discounted by up to 20pts — so a legacy-strong pick the LR distrusts
  can't beat a MLB POTD that both scorers agree on.

Runs in workflow AFTER play_of_day.py so it can OVERWRITE best_bet_{today}.
The MLB POTD write is preserved in best_bet_{today}_mlb for audit/rollback.

Usage:
  python play_of_day_multi_sport.py             # publish
  python play_of_day_multi_sport.py --dry-run   # inspect only
  python play_of_day_multi_sport.py --min-lead 5  # override lead threshold
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
K = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {'apikey': K, 'Authorization': f'Bearer {K}',
           'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


SPORT_CONTEXTS = [
    ('NFL',   'nfl_game_context'),
    ('NCAAF', 'ncaaf_game_context'),
    ('NBA',   'nba_game_context'),
    ('NCAAB', 'ncaab_game_context'),
    ('NHL',   'nhl_game_context'),
]


def _today_et() -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _fetch_sport_candidates(today: str) -> list[dict]:
    """Pull PRIME/STRONG primary_plays from each sport's context table for today."""
    candidates = []
    for sport, tbl in SPORT_CONTEXTS:
        try:
            # SELECT * — sport tables use different column names for the
            # same concept (close_home_ml vs home_ml_close, etc). Rather
            # than probe schemas, take everything; LR predictor and any
            # downstream code that reads ctx will pick what it needs.
            r = requests.get(
                f'{SB}/rest/v1/{tbl}',
                params={
                    'select': '*',
                    'game_date': f'eq.{today}',
                    'primary_play': 'not.is.null',
                },
                headers=H_READ, timeout=15,
            )
            if r.status_code != 200: continue
            rows = r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            print(f'  {sport} fetch failed: {e}')
            continue
        for row in rows:
            pp = row.get('primary_play') or {}
            if isinstance(pp, str):
                try: pp = json.loads(pp)
                except Exception: pp = {}
            if not isinstance(pp, dict): continue
            tier = str(pp.get('tier') or '').upper()
            # ══ 2026-11-04 · TIERED FALLBACK SO THERE IS ALWAYS A POTD ═══
            # ANDY: "One a day for POTD." Measured over the last 30 days
            # there was a real pick on only 20 — the other 10 read
            # 'No Play (discipline pass)' or, on two of them, 'No Games'
            # when the slate actually held 11 and 17 games.
            #
            # The chain had no fallback anywhere: play_of_day.py considers
            # MLB + NBA only, and THIS override required PRIME/STRONG. On a
            # night where nothing clears STRONG — which is most nights now
            # that NBA is 86% COVERAGE, NHL 92% and NCAAB is capped at
            # STRONG and emits LEAN — both produced nothing.
            #
            # So candidates are gathered with their tier RANK and the
            # selection below takes the best available band: PRIME/STRONG
            # first and, only if that band is empty, LEAN, then COVERAGE.
            # A lower band is never mixed with a higher one, so a COVERAGE
            # pick can only win on a night when nothing better exists.
            # PASS/SKIP are never candidates — a no-play is not a play.
            _BANDS = {'PRIME': 0, 'STRONG': 0, 'LEAN': 1, 'COVERAGE': 2}
            if tier not in _BANDS: continue
            # ══ AND A PRICE BAND, BECAUSE THE FALLBACK FOUND A 24-1 DOG ══
            # The first run of this fallback ranked 'Fairleigh Dickinson ML
            # @ +2400' top with conviction 70 — a 24-to-1 longshot against a
            # -10000 favourite — as Play of the Day. Conviction 70 on a side
            # the market prices at 4% is the model disagreeing with a
            # near-certainty, not an edge.
            #
            # POTD already has a documented ML juice gate of -200 (Andy's
            # call, 2026-09-29, reverted from -250). A ceiling without a
            # floor is half a band, so this applies both: an ML POTD must
            # price between -200 and +200. Spreads and totals are unaffected
            # -- they carry their own ~-110 and have their own gates.
            _mk = str(pp.get('type') or '').lower()
            if _mk == 'ml':
                _sd = str(pp.get('side') or '').upper()
                _px = (row.get('close_home_ml') if _sd.startswith('HOME')
                       else row.get('close_away_ml'))
                try:
                    _pxf = float(_px)
                except (TypeError, ValueError):
                    _pxf = None
                if _pxf is None:
                    print(f'    - skip {sport} {pp.get("label")!r}: ML with no '
                          f'stored price, cannot be the POTD')
                    continue
                if _pxf < -200 or _pxf > 200:
                    print(f'    - skip {sport} {pp.get("label")!r} @ {_pxf:+.0f}: '
                          f'outside the -200..+200 POTD moneyline band')
                    continue
            candidates.append({
                'sport': sport,
                'game_id': row.get('game_id'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'ctx_row': row,
                'pp': pp,
                'tier': tier,
                'band': _BANDS[tier],
                'conviction': int(pp.get('conviction') or 0),
                'side': pp.get('side'),
                'type': pp.get('type'),
                'label': pp.get('label') or '',
                'sub': pp.get('sub') or '',
            })
    return candidates


def _score_with_lr(candidate: dict) -> tuple[float | None, str]:
    """Apply sport-specific LR predictor to this candidate and return
    (p_pick_wins, notes). Adjusts conviction in-place: LR-COIN or LR-FADE
    picks get discounted so they can't overwrite a MLB POTD both scorers agree on.
    Returns (None, '') if LR unavailable for this sport."""
    try:
        from defensive_gates import (_lr_predict_ml, _LR_MODEL_MLB_ML,
                                     _LR_MODEL_NFL_ML, _LR_MODEL_NCAAF_ML,
                                     _LR_MODEL_NHL_ML, _LR_MODEL_NBA_ML)
    except Exception:
        return None, ''
    # 2026-09-19: NBA + NHL added. Both models have existed since the
    # 09-14 historical-odds backfill and defensive_gates already loads and
    # consults them in two places — this map was the only caller still
    # excluding them, so POTD candidates in those sports got NO LR
    # confirmation while the comment below claimed the models were
    # "not yet trained". models/nba_ml_logreg.json was retrained 09-17:
    # 70.4% test accuracy vs 53.5% baseline, +16.8pp on n=1,324.
    # NCAAB genuinely has no model yet and still falls through.
    model_map = {
        'MLB':   _LR_MODEL_MLB_ML,
        'NFL':   _LR_MODEL_NFL_ML,
        'NCAAF': _LR_MODEL_NCAAF_ML,
        'NHL':   _LR_MODEL_NHL_ML,
        'NBA':   _LR_MODEL_NBA_ML,
    }
    model = model_map.get(candidate['sport'])
    if model is None:
        return None, ''  # NCAAB LR not yet trained (no historical features)
    ctx = candidate['ctx_row']
    pred = _lr_predict_ml(ctx, model=model)
    if not pred:
        return None, ''
    p_home = pred['p_home_win']
    side = str(candidate['side'] or '').upper()
    p_pick = p_home if side == 'HOME' else (1 - p_home) if side == 'AWAY' else 0.5
    note = f'LR p_win={p_pick:.2f}'
    if p_pick < 0.45:
        # LR disagrees strongly — discount conviction so this can't override MLB
        candidate['conviction'] = max(0, candidate['conviction'] - 20)
        candidate['_lr_disagrees'] = True
        note += ' (discounted -20: LR fades)'
    elif p_pick < 0.55:
        candidate['conviction'] = max(0, candidate['conviction'] - 8)
        candidate['_lr_soft_disagree'] = True
        note += ' (discounted -8: LR coin)'
    else:
        note += ' (LR confirms)'
    candidate['_lr_p_pick'] = round(p_pick, 4)
    return p_pick, note


def _get_current_potd(today: str) -> dict | None:
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        params={'game_id': f'eq.best_bet_{today}', 'select': '*'},
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200: return None
    rows = r.json() if isinstance(r.json(), list) else []
    return rows[0] if rows else None


def _publish_potd_override(today: str, winner: dict, mlb_row: dict | None, dry_run: bool):
    """Write the cross-sport winner to jerry_cache.best_bet_{today}.
    Preserves the outgoing MLB POTD to best_bet_{today}_mlb for audit.

    2026-09-09 LOCK: skip if jerry_anchor_potd already published a
    Jerry-anchored decision for today (anchor='jerry_synthesis_v1').
    Jerry is the FINAL authority on POTD — cross-sport candidates
    should only overwrite if Jerry hasn't ruled yet.
    Same publish-lock pattern as sharp_card (c6912775) and
    jerry_anchor_potd (this session).
    Override with POTD_ALLOW_REPUBLISH=1 env var.
    """
    # ══ 2026-10-16 · DEFER TO THE SHARED LOCK ══
    # This script's own anchor check (below, 2026-09-09) is correct but is a
    # SECOND COPY of the rule and has no time-based hard lock, so it would
    # still overwrite after 12:00 ET whenever the anchor marker happens to be
    # absent — which is exactly how the 10-15 Guardians POTD was lost (the
    # overwrite destroyed the anchor, so nothing downstream could tell a lock
    # had ever existed). potd_publish_lock.py is the single rule; the local
    # check stays underneath as a belt-and-braces fallback.
    if not dry_run:
        try:
            from potd_publish_lock import potd_locked as _pl
            _lk, _why = _pl(today,
                            force=os.environ.get('POTD_ALLOW_REPUBLISH') == '1',
                            dry_run=False)
            if _lk:
                print(f'  🔒 cross-sport POTD override REFUSED — {_why}')
                return False
        except ImportError:
            print('  ⚠ potd_publish_lock not importable — falling back to the '
                  'local anchor check only')
        except Exception as _e:                                # noqa: BLE001
            print(f'  ⚠ shared POTD lock threw ({_e}) — local check only')

    if not dry_run and os.environ.get('POTD_ALLOW_REPUBLISH') != '1':
        try:
            _existing = mlb_row  # already fetched by caller
            if _existing:
                _data = _existing.get('data') or {}
                if isinstance(_data, str):
                    try: _data = json.loads(_data)
                    except Exception: _data = {}
                if (_data or {}).get('anchor') == 'jerry_synthesis_v1':
                    print(f'  🔒 best_bet_{today} already Jerry-anchored — '
                          f'cross-sport override skipped (Jerry rules). '
                          f'Set POTD_ALLOW_REPUBLISH=1 to bypass.')
                    return
        except Exception as _e:
            print(f'  ⚠ lock check failed: {_e} — proceeding')

    ctx = winner['ctx_row']
    if winner['side'] == 'HOME':
        ml = ctx.get('close_home_ml') or ctx.get('home_ml_close')
    else:
        ml = ctx.get('close_away_ml') or ctx.get('away_ml_close')
    pick_team = winner['home_team'] if winner['side'] == 'HOME' else winner['away_team']

    payload = {
        'sport': winner['sport'],
        'game': f"{winner['away_team']} @ {winner['home_team']}",
        'matchup': f"{winner['away_team']} @ {winner['home_team']}",
        'game_id': winner['game_id'],
        'anchor': 'cross_sport_potd_v1',
        'confidence': winner['tier'].lower(),
        'score': {
            'total': winner['conviction'],
            'source': 'cross_sport_ensemble_lr',
        },
        # ══ 2026-11-04 · THE READ ALREADY HAS A HUMAN LABEL ══════════
        # This built its own label from the internal market CODE, so an
        # NHL puck line rendered as 'Buffalo Sabres RL' - no number, and
        # 'RL' is the baseball run line, not hockey. Andy has asked about
        # this exact string before ('when primary play says knights rl
        # what does rl mean?'), and ensemble_scorer._market_label was
        # added for it but only reached the prose builders.
        #
        # primary_play.label is already correct and already carries the
        # number ('Buffalo Sabres -1.5'), so prefer it and keep the
        # code-built string only as a fallback.
        'leanDisplay': (f"{(winner['pp'].get('label') or '').strip()} "
                        f"({winner['sport']} {winner['tier']} "
                        f"{winner['conviction']}/100)"
                        if (winner['pp'].get('label') or '').strip()
                        else f"{pick_team} {(winner['type'] or 'ML').upper()} "
                             f"({winner['sport']} {winner['tier']} "
                             f"{winner['conviction']}/100)"),
        'label': winner['label'],
        'sub': winner['sub'],
        'context': {
            'sport': winner['sport'],
            'lr_p_pick_wins': winner.get('_lr_p_pick'),
            'was_mlb_potd_conviction': (mlb_row or {}).get('data', {}).get('score', {}).get('total'),
        },
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'pipelineGenerated': True,
    }

    if dry_run:
        print(f'  [DRY] would OVERRIDE best_bet_{today} with:')
        print(f'    sport={payload["sport"]} · {payload["leanDisplay"]}')
        return

    # Preserve outgoing MLB POTD for audit / rollback
    if mlb_row and mlb_row.get('data', {}).get('sport') == 'MLB':
        try:
            requests.post(
                f'{SB}/rest/v1/jerry_cache?on_conflict=cache_key',
                headers=H_WRITE, timeout=15,
                json={
                    'game_id': f'best_bet_{today}_mlb',
                    'sport': 'MLB',
                    'cache_key': f'best_bet_{today}_mlb',
                    'data': mlb_row.get('data'),
                    'narrative': mlb_row.get('narrative'),
                    'fetched_at': datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as e:
            print(f'  ⚠ mlb-backup write failed (non-fatal): {e}')

    # ══ 2026-11-04 · merge-duplicates WITHOUT on_conflict IS INSERT-ONLY ══
    # Both jerry_cache writes here posted to the bare table. The Prefer
    # header asks for merge-duplicates, but PostgREST needs an on_conflict
    # target to use it, so every write that landed on an existing
    # best_bet_{today} row came back 409 23505 on
    # jerry_cache_cache_key_key — and the function returns on a publish
    # failure, so the history write never ran either.
    #
    # That means this override could only ever CREATE a POTD, never
    # replace one. It went unnoticed because the override normally fires
    # on a night with no row, or on the empty placeholder. It surfaced the
    # moment a refresh was needed: the corrupted-weights row would not
    # clear. Every other jerry_cache writer in the pipeline already uses
    # on_conflict=cache_key.
    r = requests.post(
        f'{SB}/rest/v1/jerry_cache?on_conflict=cache_key',
        headers=H_WRITE, timeout=15,
        json={
            'game_id': f'best_bet_{today}',
            'sport': winner['sport'],
            'cache_key': f'best_bet_{today}',
            'data': payload,
            'narrative': (winner['sub'] or winner['label'])[:500],
            'fetched_at': datetime.now(timezone.utc).isoformat(),
        },
    )
    if r.status_code in (200, 201, 204):
        print(f'  ✓ published cross-sport POTD: {winner["sport"]} {payload["leanDisplay"]}')
    else:
        print(f'  ✗ publish failed {r.status_code}: {r.text[:200]}')
        return

    # ══ 2026-11-04 · THE RECORD, NOT JUST THE CACHE ═════════════════════
    # This wrote jerry_cache only. play_of_day.py writes BOTH jerry_cache
    # and daily_best_bet_history, so an MLB POTD enters the record and a
    # cross-sport one did not — reconcile_potd_surfaces then reports
    # "no history row exists — cache-only publish, skipping", because it
    # reconciles rows and does not create them.
    #
    # A POTD that exists only in the cache is invisible to the Receipts
    # calendar, to grading and to every record query. On a night like
    # tonight, when the cross-sport override IS the POTD, that means no
    # POTD in the record at all — part of why only 20 of the last 30 days
    # carry a real pick.
    #
    # PRICE. A spread or total POTD has no per-side price in these context
    # tables (NCAAB ctx carries close_spread but no spread price), and an
    # unpriced row cannot be graded for ROI — tonight's other work was
    # repairing 113 priceless POTD receipts. So a side market defaults to
    # -110, which is the median of our own priced POTD spreads/totals, and
    # the assumption is recorded in the row rather than left implicit.
    _mk = str(winner['pp'].get('type') or '').lower()
    _sd = str(winner['pp'].get('side') or '').upper()
    _ctx = winner.get('ctx_row') or {}
    _odds = None
    _assumed = False
    if _mk == 'ml':
        _odds = (_ctx.get('close_home_ml') if _sd.startswith('HOME')
                 else _ctx.get('close_away_ml'))
    # ══ 2026-11-04 · NHL STORES THE PUCK-LINE PRICE, SO USE IT ══════════
    # Only ML was looked up, so an NHL rl pick fell to the -110 default
    # even though its real price sits on the same ctx row. Tonight's POTD
    # is Buffalo -1.5, and close_puckline_home_price is +180. Recording
    # that at -110 is not a rounding error - it understates a winning
    # unit by 2.6x and makes every ROI figure built on it wrong.
    #
    # A puck line is nothing like a -110 spread: the favourite laying
    # -1.5 is a PLUS-money bet (home prices tonight: +180, +103, +170,
    # +150) because it has to win by two. The -110 default was imported
    # from football/basketball spreads, where it is a fair median.
    if _odds is None and _mk == 'rl' and str(winner.get('sport')) == 'NHL':
        _odds = (_ctx.get('close_puckline_home_price') if _sd.startswith('HOME')
                 else _ctx.get('close_puckline_away_price'))
    if _odds is None:
        _odds = -110
        _assumed = True
    try:
        _odds = int(float(_odds))
    except (TypeError, ValueError):
        _odds, _assumed = -110, True

    _hist = {
        'bet_date': today,
        'sport': winner['sport'],
        'game': f"{winner['away_team']} @ {winner['home_team']}",
        'lean': payload['leanDisplay'],
        'sweat_score': int(winner.get('conviction') or 0),
        'result': 'Pending',
        'odds_american': _odds,
    }
    hr = requests.post(
        f'{SB}/rest/v1/daily_best_bet_history?on_conflict=bet_date',
        headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        timeout=15, json=_hist,
    )
    if hr.status_code not in (200, 201, 204):
        print(f'  ✗ history write failed {hr.status_code}: {hr.text[:200]}')
        return
    # A 204 is not a write — read it back.
    back = requests.get(
        f'{SB}/rest/v1/daily_best_bet_history',
        headers=H_READ, timeout=15,
        params={'select': 'bet_date,sport,lean,odds_american',
                'bet_date': f'eq.{today}'},
    ).json() or []
    if not back:
        print('  ✗ history row NOT confirmed on readback — the POTD would be '
              'cache-only and invisible to the record')
        return
    print(f'  ✓ history row: {back[0]["sport"]} {back[0]["lean"][:44]} '
          f'@ {back[0]["odds_american"]}'
          + ('  (price ASSUMED -110: no per-side price stored for a '
             f'{_mk} pick)' if _assumed else ''))


def run(dry_run: bool = False, min_lead: int = 5):
    today = _today_et()
    print(f'== Cross-sport POTD scan · {today} ==')

    mlb_row = _get_current_potd(today)
    mlb_conviction = (mlb_row or {}).get('data', {}).get('score', {}).get('total', 0) or 0
    mlb_sport = (mlb_row or {}).get('data', {}).get('sport', 'NONE')
    print(f'  Current best_bet_{today}: sport={mlb_sport} conviction={mlb_conviction}')

    candidates = _fetch_sport_candidates(today)
    # Keep only the best band present, so a COVERAGE pick never competes
    # with a STRONG one — it only fills a night that has nothing better.
    if candidates:
        _best_band = min(c['band'] for c in candidates)
        _by_band = {}
        for _c in candidates:
            _by_band.setdefault(_c['band'], 0)
            _by_band[_c['band']] += 1
        candidates = [c for c in candidates if c['band'] == _best_band]
        _names = {0: 'PRIME/STRONG', 1: 'LEAN', 2: 'COVERAGE'}
        print(f'  Cross-sport candidates by band: '
              + ', '.join(f'{_names[b]}={n}' for b, n in sorted(_by_band.items())))
        if _best_band > 0:
            print(f'  ⬆ no PRIME/STRONG tonight — falling back to the '
                  f'{_names[_best_band]} band so there is still a POTD '
                  f'(ANDY: one a day)')
    print(f'  Cross-sport candidates (best band): {len(candidates)}')
    if not candidates:
        print('  no non-MLB pick in ANY band today — leaving MLB POTD as-is')
        return 0

    # Apply LR gate to each candidate — updates conviction in-place
    scored = []
    for c in candidates:
        p, note = _score_with_lr(c)
        c['_lr_note'] = note
        scored.append(c)

    # Sort by (post-LR) conviction desc
    scored.sort(key=lambda x: -x['conviction'])
    print('  Top 5 cross-sport candidates after LR gate:')
    for c in scored[:5]:
        print(f'    {c["sport"]:6s} {c["tier"]:6s} c={c["conviction"]:>3d}  '
              f'{c["away_team"][:20]:20s} @ {c["home_team"][:20]:20s}  {c["_lr_note"]}')

    winner = scored[0]
    lead = winner['conviction'] - mlb_conviction
    print(f'\n  Cross-sport winner conviction: {winner["conviction"]} vs MLB POTD {mlb_conviction} '
          f'(lead={lead:+d}, min_lead={min_lead})')

    # If MLB POTD already IS the winner or the cross-sport lead isn't material, keep MLB
    # ══ 2026-11-04 · A PLACEHOLDER IS NOT A PICK ════════════════════════
    # This guard exists so the override cannot stomp a POTD written by
    # something other than the MLB pipeline — a hand-set pick, say. But it
    # also fired on the EMPTY placeholder play_of_day.py writes when it has
    # no candidates: {"noGames": true}, sport='none', conviction 0.
    #
    # That is how 10 of the last 30 days ended with no POTD. Two of them
    # even told users 'No Games' while the slate held 11 and 17 games —
    # play_of_day.py only looks at MLB + NBA, so 'no candidates' was
    # reported as 'no games'. The override had a real pick in hand and
    # refused to write it because a placeholder was in the way.
    #
    # A placeholder has nothing to protect: no sport, no conviction, and
    # noGames set. Anything else still blocks the override.
    _ph = (str(mlb_sport or '').upper() in ('', 'NONE')
           and int(mlb_conviction or 0) == 0)
    # 2026-11-04: this override must also be able to REFRESH ITS OWN prior
    # write. It stamps anchor='cross_sport_potd_v1', so its own row is
    # identifiable and is not the "non-pipeline write" the guard protects.
    #
    # Found the hard way: a corrupted weight run published 'Santa Clara RL',
    # the weights were fixed, the re-resolve changed that game's pick — and
    # the override refused to update because the row it had written itself
    # was now "not MLB". A stale POTD that no longer matches its own context
    # row is worse than either outcome.
    _own = str(((mlb_row or {}).get('data') or {}).get('anchor')
               or '') == 'cross_sport_potd_v1'
    if _ph:
        print('  Current POTD is an empty placeholder (sport=%s conv=%s) — '
              'overriding it is the point, not a stomp'
              % (mlb_sport, mlb_conviction))
    if _own:
        print('  Current POTD is this override own prior write '
              '(%s conv=%s) — refreshing it, not competing with it'
              % (mlb_sport, mlb_conviction))
    if mlb_sport != 'MLB' and not (_ph or _own):
        print('  Current POTD is not MLB — skipping override to avoid stomping non-pipeline write')
        return 0
    # ══ 2026-11-04 · min_lead CANNOT APPLY TO A SELF-REFRESH ════════════
    # lead = winner.conviction - mlb_conviction, and when the incumbent IS
    # this override's own earlier write both numbers come from the same
    # scorer. The comparison then asks whether tonight's engine beats its
    # own earlier run by 5 points, which is not a question min_lead was
    # written to answer: min_lead exists so a marginally-better non-MLB
    # pick does not displace a legitimate MLB POTD.
    #
    # Measured tonight: the incumbent was NCAAB Santa Clara RL at
    # conviction 62, published under the corrupted hit_rate weights. The
    # corrected re-resolve dropped that game to conviction 50 and the best
    # candidate on the slate to 56, so lead = -6 and the stale row that no
    # longer matches ANY current context row held the surface.
    #
    # A refresh replaces; it does not win an election.
    if lead < min_lead and not _own:
        print(f'  Lead {lead} < threshold {min_lead} — keeping MLB POTD')
        return 0

    _publish_potd_override(today, winner, mlb_row, dry_run)
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--min-lead', type=int, default=5,
                    help='Minimum conviction lead over MLB POTD to trigger override')
    args = ap.parse_args()
    sys.exit(run(dry_run=args.dry_run, min_lead=args.min_lead))
