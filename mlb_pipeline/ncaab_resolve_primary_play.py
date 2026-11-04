"""NCAAB 4-lens primary_play resolver (2026-08-14).

Session 4 of NCAAB 5-lens build (4 lenses for launch; V4 XGBoost
deferred to Feb 2027 per project_ncaab_v4_deferred_814).

POST-HOC PATCH pattern: runs AFTER mc_probabilities + panel_prediction
are written to ncaab_game_context. Reads the row + both blobs,
recomputes sweat_score/tier/primary_play consuming all lenses,
PATCHes result back to same row.

Isolated from ncaab_game_context.build_context_row so:
  - MC + Panel can fail independently without blocking game_context
  - Same pattern as MLB apply_refit_verdict_override
  - Backtest replay works by calling resolver on any row

LENS INVENTORY (production 2026-08-14)
  1. KenPom-in-context: projected_spread/projected_total/confluence_net
     (existing, from ncaab_game_context.py compute_projections/confluence)
  2. MC: mc_probabilities.mc_expected_margin/mc_expected_total/mc_p_home
  3. Panel: panel_prediction.panel_projected_margin/panel_projected_total
  4. Jerry: read from ncaab_jerry_reads.recommended_pick if present

  V4 XGBoost slot reserved for Feb 2027 when we have accumulated
  our own daily rating snapshots.

RESOLVER LOGIC

  Side signal per lens (home / away vote):
    kenpom: sign(projected_spread)      home if >0
    mc:     mc_p_home > 0.5             home if >0.5
    panel:  sign(panel_projected_margin) home if >0
    jerry:  parse "TEAM XX" -> which team

  Lens agreement count for majority side:
    4/4 = PRIME confluence
    3/4 = STRONG confluence
    2/4 = split (LEAN or PASS depending on edge magnitude)
    <2  = PASS

  Total signal per lens (over / under vote):
    kenpom: projected_total vs close_total (>= 3pt diff to count)
    mc:     mc_expected_total vs close_total (>= 3pt to count)
    panel:  panel_projected_total vs close_total (>= 3pt to count)

TIER PROMOTIONS (over old KenPom-only compute_primary_play)
  * 4/4 lens agreement on ML side + edge >= 2.0 -> PRIME
  * 3/4 lens agreement on ML side + edge >= 2.5 -> PRIME
  * 3/4 lens agreement on TOTAL + |edge| >= 5.0 -> PRIME total
  * 4/4 lens agreement on TOTAL + |edge| >= 3.5 -> STRONG total

TIER DEMOTIONS
  * <2 lens agreement -> primary_play = None (holdout)
  * Panel confidence=low -> cap tier at STRONG (never PRIME)
  * MC low confidence + <3 lens agreement -> cap at STRONG

CLI
  python ncaab_resolve_primary_play.py                    # today
  python ncaab_resolve_primary_play.py --game-date 2026-11-04
  python ncaab_resolve_primary_play.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, re
from datetime import date, datetime, timezone
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

#: The moneyline tier ceiling lives in ncaab_game_context so both writers
#: of primary_play share one number. See the measurement table beside it,
#: and the note in resolve() below for why this file needed it too.
from ncaab_game_context import NCAAB_ML_TIER_CAP  # noqa: E402

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Tier cutoffs match ncaab_game_context.sweat_tier
PRIME_FLOOR = 80
STRONG_FLOOR = 65
LIGHT_FLOOR = 50


def _side_votes(ctx: dict, mc: Optional[dict], panel: Optional[dict],
                jerry_pick: Optional[str]) -> dict:
    """Return {'home': [lens_names], 'away': [lens_names]}."""
    votes = {'home': [], 'away': [], 'no_signal': []}

    # KenPom: projected_spread (home perspective, +ve = home favored)
    ps = ctx.get('projected_spread')
    if ps is not None:
        votes['home' if float(ps) > 0 else 'away'].append('kenpom')
    else:
        votes['no_signal'].append('kenpom')

    # MC: mc_p_home > 0.5
    if mc and mc.get('mc_p_home') is not None:
        votes['home' if float(mc['mc_p_home']) > 0.5 else 'away'].append('mc')
    else:
        votes['no_signal'].append('mc')

    # Panel: panel_projected_margin (+ve = home favored)
    if panel and panel.get('panel_projected_margin') is not None:
        votes['home' if float(panel['panel_projected_margin']) > 0 else 'away'].append('panel')
    else:
        votes['no_signal'].append('panel')

    # Jerry: parse "TEAM label" or "TEAM ML" — home if matches home_team
    if jerry_pick:
        home_team = (ctx.get('home_team') or '').lower()
        away_team = (ctx.get('away_team') or '').lower()
        j = jerry_pick.lower()
        if home_team and home_team in j: votes['home'].append('jerry')
        elif away_team and away_team in j: votes['away'].append('jerry')
        else: votes['no_signal'].append('jerry')
    else:
        votes['no_signal'].append('jerry')

    return votes


def _total_votes(ctx: dict, mc: Optional[dict], panel: Optional[dict],
                 min_edge: float = 3.0) -> dict:
    """Over/under votes per lens with min edge threshold."""
    votes = {'over': [], 'under': [], 'no_signal': []}
    close_total = ctx.get('close_total') or ctx.get('open_total')
    if close_total is None:
        return {'over': [], 'under': [], 'no_signal': ['kenpom', 'mc', 'panel']}
    close_total = float(close_total)

    pt = ctx.get('projected_total')
    if pt is not None:
        diff = float(pt) - close_total
        if abs(diff) >= min_edge:
            votes['over' if diff > 0 else 'under'].append('kenpom')
        else: votes['no_signal'].append('kenpom')
    else: votes['no_signal'].append('kenpom')

    if mc and mc.get('mc_expected_total') is not None:
        diff = float(mc['mc_expected_total']) - close_total
        if abs(diff) >= min_edge:
            votes['over' if diff > 0 else 'under'].append('mc')
        else: votes['no_signal'].append('mc')
    else: votes['no_signal'].append('mc')

    if panel and panel.get('panel_projected_total') is not None:
        diff = float(panel['panel_projected_total']) - close_total
        if abs(diff) >= min_edge:
            votes['over' if diff > 0 else 'under'].append('panel')
        else: votes['no_signal'].append('panel')
    else: votes['no_signal'].append('panel')

    return votes


def resolve(ctx: dict, mc: Optional[dict], panel: Optional[dict],
            jerry_pick: Optional[str] = None) -> dict:
    """Return {sweat_score, sweat_tier, primary_play, lens_meta}."""
    proj_spread = ctx.get('projected_spread')
    close_spread = ctx.get('close_spread') or ctx.get('open_spread')
    close_total = ctx.get('close_total') or ctx.get('open_total')
    proj_total = ctx.get('projected_total')
    home_team = ctx.get('home_team') or 'Home'
    away_team = ctx.get('away_team') or 'Away'
    conf_net = ctx.get('signal_confluence_net') or 0

    spread_edge = None
    if proj_spread is not None and close_spread is not None:
        spread_edge = round(float(proj_spread) + float(close_spread), 2)
    abs_edge = abs(spread_edge) if spread_edge is not None else 0.0

    total_edge = None
    if proj_total is not None and close_total is not None:
        total_edge = round(float(proj_total) - float(close_total), 2)

    side_votes = _side_votes(ctx, mc, panel, jerry_pick)
    total_votes = _total_votes(ctx, mc, panel, min_edge=3.0)

    # Winning side per majority
    home_count = len(side_votes['home'])
    away_count = len(side_votes['away'])
    total_lens_signal = home_count + away_count
    majority_side = 'home' if home_count > away_count else ('away' if away_count > home_count else 'split')
    majority_count = max(home_count, away_count)
    fav_team = home_team if majority_side == 'home' else (away_team if majority_side == 'away' else None)

    # Base score (KenPom-driven, existing formula)
    score = 40
    if abs_edge >= 4.0: score += 20
    elif abs_edge >= 3.0: score += 15
    elif abs_edge >= 2.0: score += 10
    elif abs_edge >= 1.0: score += 5

    if abs(conf_net) >= 5: score += 10
    elif abs(conf_net) >= 4: score += 8
    elif abs(conf_net) >= 3: score += 5
    elif abs(conf_net) >= 2: score += 3

    # Lens confluence boost (the new signal)
    if majority_count >= 4: score += 15   # unanimous 4/4
    elif majority_count == 3: score += 10  # 3/4 majority
    elif majority_count == 2 and (home_count == 0 or away_count == 0): score += 5

    # MC HIGH-CONF boost when it agrees with majority
    if mc and mc.get('mc_confidence_high'):
        mc_side = 'home' if mc.get('mc_p_home', 0.5) > 0.5 else 'away'
        if mc_side == majority_side: score += 5

    # Panel HIGH-CONF boost when it agrees
    if panel and panel.get('panel_confidence') == 'high':
        panel_side = 'home' if (panel.get('panel_projected_margin') or 0) > 0 else 'away'
        if panel_side == majority_side: score += 5

    # Total lens agreement boost
    total_majority = max(len(total_votes['over']), len(total_votes['under']))
    if total_majority >= 3: score += 8
    elif total_majority == 2: score += 3

    score = min(100, max(0, score))

    # Tier assignment with lens-based caps
    if score >= PRIME_FLOOR: tier = 'PRIME'
    elif score >= STRONG_FLOOR: tier = 'STRONG'
    elif score >= LIGHT_FLOOR: tier = 'LIGHT_LEAN'
    else: tier = 'PASS'

    # Demotion rules
    if panel and panel.get('panel_confidence') == 'low' and tier == 'PRIME':
        tier = 'STRONG'  # Panel disagreement caps at STRONG
    if majority_count < 2 and tier in ('PRIME', 'STRONG'):
        tier = 'LIGHT_LEAN'  # Not enough lens agreement

    # ══ 2026-11-02 · THIS IS THE WRITER THAT WINS, AND IT WAS UNCAPPED ══
    # ncaab_game_context.compute_primary_play got a moneyline tier cap first.
    # It was the wrong file: this resolver is a POST-HOC PATCH that runs
    # afterwards and REPLACES primary_play, so every stored badge came from
    # here. The stored blobs proved it -- signal_floor 60 and a
    # "{n}/4 lens lean" sub, which only rule 3 below emits
    # (feedback_fix_every_writer_to_the_table).
    #
    # WHAT WAS ON THE BOARD, visible from today because the game_date repair
    # put the real slate on the right day:
    #     69 ml badges on 2026-11-02, 62 of them STRONG
    #     61 with a price: -100000 .. -105, MEDIAN -10000
    #     ten at -100000 on 28.5-to-40.5-point favourites
    #
    # MEASURED, 4,981 NCAAB favourite-ML plays with a close price and a
    # final score, ROI at each row's own price:
    #     -100000..-1000  n=930  95.70% vs BE 95.78%   -0.10%
    #       -1000..-500   n=573  86.04% vs BE 87.13%   -1.22%
    #        -500..-300   n=769  77.89% vs BE 79.00%   -1.41%
    #        -300..-250   n=390  70.26% vs BE 73.40%   -4.29%
    #        -250..-200   n=501  69.26% vs BE 69.23%   +0.01%
    #        -200..-150   n=853  60.38% vs BE 63.26%   -4.44%
    #        -150..-100   n=959  53.49% vs BE 56.37%   -5.08%
    # Not one band clears breakeven; the heavy end holds split-half by date
    # (-1.20% n=1136 / -0.45% n=1136). So a juice threshold would have been
    # the wrong shape of fix -- there is no price at which to let it through.
    #
    # And our own selection is UNMEASURABLE: ncaab_game_results carries
    # projected_spread on 0 of 11,529 rows, and every adj_em field on 0, so
    # there is no way to show these four lenses beat the blanket control
    # above (feedback_unmeasured_signal_must_not_outweigh_measured).
    #
    # Capped, not deleted -- same discipline as NCAAB_TOTAL_TIER_CAP. The
    # pick keeps its side, label and number, stays on the game card, and a
    # shadow record accumulates so this can lift on evidence. Lifting it
    # first requires persisting projected_spread into ncaab_game_results.
    #
    # `side` IS NOW RECORDED. The 2026-10-30 fix added it to
    # compute_primary_play and not here, so this writer overwrote it away
    # again -- which is why The Sharp still dropped NCAAB ML picks as
    # "no book moneyline" and why a price lookup on the stored rows returned
    # None on all 64. majority_side is the side; it was known here all along.
    ml_side = ('HOME' if majority_side == 'home'
               else 'AWAY' if majority_side == 'away' else None)
    _ML_CAP_WHY = ('NCAAB favourite ML is negative or flat in every price '
                   'band, n=4,981; and our own selection is unmeasurable '
                   '(projected_spread 0 of 11,529 result rows)')

    # Primary play selection
    primary_play = None

    # Edge attribution guard: spread_edge is KenPom-derived. If KenPom is
    # in the minority (dissents from majority side), using its edge to
    # justify the majority pick is contradictory — the edge magnitude
    # points the WRONG direction for the majority. Only use edge as a
    # promotion signal when KenPom is IN the majority.
    kenpom_in_majority = 'kenpom' in side_votes[majority_side] if majority_side != 'split' else False
    effective_edge = abs_edge if kenpom_in_majority else 0.0

    # 1. PRIME/STRONG ML with 3+/4 lens agreement + KenPom-supported edge
    if majority_count >= 3 and effective_edge >= 2.0 and fav_team and tier in ('PRIME', 'STRONG'):
        primary_play = {
            'type': 'ml',
            'tier': NCAAB_ML_TIER_CAP,
            'side': ml_side,
            'label': f'{fav_team} ML',
            'sub': f'{majority_count}/4 lens agreement, {effective_edge:.1f}pt edge (kenpom aligned)',
            'signal_floor': 85 if tier == 'PRIME' else 72,
            'lenses_agreeing': side_votes[majority_side],
            'lenses_dissenting': side_votes['home' if majority_side == 'away' else 'away'],
            '_ml_cap': {'from': tier, 'to': NCAAB_ML_TIER_CAP,
                        'why': _ML_CAP_WHY},
        }
    # 1b. 3+/4 lens agreement AGAINST kenpom — still shippable via lens count,
    # but no KenPom-edge promotion (lens count alone drives tier)
    elif majority_count >= 3 and not kenpom_in_majority and fav_team and tier in ('STRONG',):
        primary_play = {
            'type': 'ml',
            'tier': NCAAB_ML_TIER_CAP,
            'side': ml_side,
            'label': f'{fav_team} ML',
            'sub': f'{majority_count}/4 lens agreement (kenpom dissents — {majority_count}-lens override)',
            'signal_floor': 70,
            'lenses_agreeing': side_votes[majority_side],
            'lenses_dissenting': side_votes['home' if majority_side == 'away' else 'away'],
            '_ml_cap': {'from': 'STRONG', 'to': NCAAB_ML_TIER_CAP,
                        'why': _ML_CAP_WHY},
        }
    # 2. STRONG total with 3+/3 lens agreement (Jerry doesn't vote totals)
    elif total_majority >= 3 and total_edge is not None and abs(total_edge) >= 3.5:
        side = 'Over' if total_edge > 0 else 'Under'
        primary_play = {
            'type': 'total',
            'tier': tier if tier != 'PASS' else 'STRONG',
            'label': f'{side} {close_total}',
            'sub': f'{total_majority}/3 total lens agreement, {abs(total_edge):.1f}pt edge',
            'signal_floor': 78,
            'lenses_agreeing': total_votes['over' if total_edge > 0 else 'under'],
        }
    # 3. LIGHT ML with majority agreement (edge respects KenPom alignment)
    elif majority_count >= 2 and effective_edge >= 1.5 and fav_team and tier != 'PASS':
        primary_play = {
            'type': 'ml',
            'tier': NCAAB_ML_TIER_CAP,
            'side': ml_side,
            'label': f'{fav_team} ML lean',
            'sub': f'{majority_count}/4 lens lean, {effective_edge:.1f}pt edge',
            'signal_floor': 60,
            'lenses_agreeing': side_votes[majority_side],
            '_ml_cap': {'from': tier, 'to': NCAAB_ML_TIER_CAP,
                        'why': _ML_CAP_WHY},
        }
    # 4. LIGHT total
    elif total_majority >= 2 and total_edge is not None and abs(total_edge) >= 3.0 and tier != 'PASS':
        side = 'Over' if total_edge > 0 else 'Under'
        primary_play = {
            'type': 'total',
            'tier': tier,
            'label': f'{side} {close_total}',
            'sub': f'{total_majority}/3 total lens lean, {abs(total_edge):.1f}pt edge',
            'signal_floor': 55,
        }

    lens_meta = {
        'side_votes': {k: v for k, v in side_votes.items() if v},
        'total_votes': {k: v for k, v in total_votes.items() if v},
        'majority_side': majority_side,
        'majority_count_side': majority_count,
        'majority_count_total': total_majority,
        'lenses_present': {
            'kenpom': proj_spread is not None,
            'mc': mc is not None,
            'panel': panel is not None,
            'jerry': jerry_pick is not None,
        },
        'resolver_version': '4lens_v1_2026-08-14',
    }

    return {
        'sweat_score': int(score),
        'sweat_tier': tier,
        'primary_play': primary_play,
        'lens_meta': lens_meta,
    }


def _load_games(game_date: date) -> list:
    fields = ('game_id,home_team,away_team,projected_spread,projected_total,'
              'close_spread,close_total,open_spread,open_total,'
              'signal_confluence_net,mc_probabilities,panel_prediction')
    r = requests.get(
        f'{SB}/rest/v1/ncaab_game_context?select={fields}'
        f'&game_date=eq.{game_date.isoformat()}',
        headers=H_READ, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ fetch: {r.status_code} {r.text[:200]}')
        return []
    return r.json() or []


def _load_jerry(game_date: date) -> dict:
    """Return {game_id: recommended_pick} from ncaab_jerry_reads if table exists."""
    r = requests.get(
        f'{SB}/rest/v1/ncaab_jerry_reads?select=game_id,recommended_pick'
        f'&game_date=eq.{game_date.isoformat()}',
        headers=H_READ, timeout=15)
    if r.status_code != 200: return {}
    return {row.get('game_id'): row.get('recommended_pick')
            for row in (r.json() or []) if row.get('game_id')}


def _write(game_id, result: dict) -> bool:
    payload = {
        'sweat_score': result['sweat_score'],
        'sweat_tier': result['sweat_tier'],
        'primary_play': result['primary_play'],
    }
    r = requests.patch(
        f'{SB}/rest/v1/ncaab_game_context?game_id=eq.{requests.utils.quote(str(game_id), safe="")}',
        headers=H_WRITE, json=payload, timeout=15)
    return r.status_code in (200, 204)


def run(game_date: Optional[date] = None, dry_run: bool = False) -> int:
    if game_date is None:
        # 2026-11-03: ET, not UTC. datetime.now(timezone.utc).date() rolls at
        # 19:00 ET (20:00 in summer), so every run after the evening tip-off
        # window silently worked TOMORROW's slate and never touched today's.
        # Measured at 20:46 ET on 11-03: the panel reported '2026-11-04' and
        # scored the 10 games on the 4th while the 19 games of the 3rd kept
        # whatever they already had. With GitHub Actions running 7-10h late
        # (project_card_builds_before_every_other_sport_1102) the 20:00 UTC
        # cron lands past 00:00 UTC routinely, so this is not a rare edge.
        # sweat_dates exists for exactly this and its docstring already names
        # three NFL instances of the same bug.
        from sweat_dates import et_today
        game_date = date.fromisoformat(et_today())
    print(f'=== ncaab_resolve_primary_play · {game_date.isoformat()} ===')

    games = _load_games(game_date)
    print(f'  games: {len(games)}')
    if not games:
        print('  ° no games — resolver no-op'); return 0

    jerry = _load_jerry(game_date)
    print(f'  jerry picks: {len(jerry)}')

    n_ok = 0; tier_counts = {}
    for g in games:
        result = resolve(g, g.get('mc_probabilities'), g.get('panel_prediction'),
                          jerry.get(g.get('game_id')))
        # ═══ 2026-11-03 · ENSEMBLE MERGE, NOT REPLACE ═══════════════════
        # Andy: "we need to have a baseline for ncaab models and overall
        # engine (decision makers) ... I need a product."
        #
        # ensemble_scorer has supported NCAAB all along (MARKETS_BY_SPORT
        # lists ml/rl/total) -- this file simply never called it, so NCAAB
        # was the one sport whose picks came from a bespoke lens resolver
        # with NO conviction key and NO per-signal attribution, which is
        # why 0 of 128 NCAAB jerry_reads carry a conviction.
        #
        # MERGED, deliberately. project_lr_v1_override_erases_attribution_
        # 1027 measured a second engine rewriting primary_play on 457 picks
        # and destroying _ensemble_sources, and losing on the side flips it
        # made. So the ensemble's pick is taken for the fields it is better
        # at -- tier, conviction, per-signal attribution -- while this
        # resolver's lens agreement is preserved under `_lens_meta`, and if
        # the ensemble has no opinion the bespoke result stands untouched.
        #
        # NCAAB is capped at STRONG in MAX_TIER_BY_SPORT: of its 65 signals
        # exactly 2 are PROVISIONAL, and a 2-signal library must not be able
        # to make a 2-unit PRIME claim.
        try:
            from ensemble_scorer import score_game as _ens
            _dec = _ens('NCAAB', g)
            if _dec is not None:
                _top = _dec.top()
                if _top is not None and _top.pick is not None:
                    _prev = result.get('primary_play') or {}
                    result['primary_play'] = {
                        'type': _top.market,
                        'tier': _top.tier,
                        'label': _top.display_label,
                        'side': _top.side,
                        'line': _top.line,
                        'conviction': _top.conviction,
                        'score': round(_top.score, 2),
                        'sub': getattr(_top, 'display_prose', None) or _prev.get('sub'),
                        '_engine': 'ensemble_v2',
                        '_ensemble_sources': [
                            {'signal_key': c.signal_key,
                             'class': c.signal_class,
                             'side': c.side,
                             'weight': round(c.weight, 3),
                             'n': c.n,
                             'contribution': round(c.contribution, 3)}
                            for c in (_top.contributions or [])[:8]
                        ],
                        # nothing the lens resolver knew is thrown away
                        '_lens_meta': result.get('lens_meta'),
                        '_pre_ensemble': {k: _prev.get(k)
                                          for k in ('type', 'tier', 'label',
                                                    'side', 'line')},
                    }
                    result['sweat_tier'] = _top.tier
        except ImportError:
            pass          # ensemble absent — bespoke result stands
        except Exception as _e:                              # noqa: BLE001
            # Never let the ensemble take down the resolver that works.
            print(f'  ⚠ ensemble unavailable for {g.get("game_id")}: '
                  f'{type(_e).__name__}: {_e}')

        tier_counts[result['sweat_tier']] = tier_counts.get(result['sweat_tier'], 0) + 1

        if dry_run:
            pp = result.get('primary_play')
            print(f'  [DRY] {g.get("away_team")} @ {g.get("home_team")}: '
                  f'{result["sweat_tier"]} {result["sweat_score"]} | '
                  f'{pp["label"] if pp else "no play"} '
                  f'| lenses side={result["lens_meta"]["majority_count_side"]}/4 '
                  f'total={result["lens_meta"]["majority_count_total"]}/3')
            n_ok += 1
        else:
            if _write(g['game_id'], result):
                n_ok += 1

    print(f'  ✓ {n_ok} rows resolved; tier breakdown: {tier_counts}')
    return n_ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--game-date', help='YYYY-MM-DD; defaults to today')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    gd = date.fromisoformat(args.game_date) if args.game_date else None
    run(gd, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
