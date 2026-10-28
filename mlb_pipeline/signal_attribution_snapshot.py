"""Snapshot every fired signal per game into signal_attribution.

Andy 9/13: "we should be tracking what each says and keeping record."
Per-game per-signal audit trail so we can measure whether each signal
we surface (EPA gap, LR shadow, GOAT, cohorts, anchor, etc.) actually
predicts the pick side hitting.

Runs post-Sharp-Card-lock (11am ET) — captures the FROZEN version of
primary_play + signals that the user saw. Grader then resolves each
snapshot against the final result.

Signals captured per game (NFL + NCAAF):
  EPA_GAP        — home_off_epa_pp - away_off_epa_pp (kind: ok|warn)
  LR_SHADOW      — primary_play._lr_ml_shadow (side + p_home_win)
  GOAT           — align_status.chips_extra where key='goat'
  ANCHOR         — spread_anchor_weight (0 = off, 0.4 = mid, 0.75 = heavy)
  COHORT_<tag>   — each cohort_tag from ctx.cohort_tags
  CPOE_GAP       — QB CPOE differential (when available)

Run:
  python signal_attribution_snapshot.py                 # today, NFL + NCAAF
  python signal_attribution_snapshot.py --sport NFL
  python signal_attribution_snapshot.py --date 2026-09-13
  python signal_attribution_snapshot.py --dry-run
"""
import argparse
import os
import sys
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

for _p in ('mlb_pipeline/.env', '.env'):
    if os.path.exists(_p):
        load_dotenv(_p)
        break

SB = os.environ.get('SUPABASE_URL')
K = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
     or os.environ.get('SUPABASE_KEY'))
if not (SB and K):
    sys.exit('SUPABASE_URL / SUPABASE_KEY not set')

H_READ = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


def resolve_week(ctx: dict, game_date) -> Optional[int]:
    """The week this pick belongs to, for the weekly attribution cadence.

    ══ 2026-10-27 · WHY A FALLBACK CHAIN ═══════════════════════════════════
    This used to read `ctx.get('season_week')` alone, which left
    season_week NULL on 1,246 of 2,165 signal_attribution rows (58%) and so
    made Andy's weekly review impossible on most of the data.

    The NULLs are TWO different causes and must not be treated as one:

      * NCAAF is a plain bug. `ncaaf_game_context` has `week` populated on
        650 of 660 rows but `season_week` on only 256, and this function was
        reading the emptier of the two columns. 403 of the 404 NULL rows are
        recoverable from `week`, which this query ALREADY SELECTS.

      * MLB / NHL / NBA have NO week column at all, and for MLB that is
        CORRECT -- baseball has no league weeks, so a NULL there is honest
        data, not a defect. Filling it from a league week would be inventing
        a field. Those sports group by the ISO week of game_date instead.

    So: season_week, else week, else the ISO week number of game_date. The
    last branch means a weekly cadence is available for EVERY sport,
    including the ones with no league week -- it is a grouping key, and
    every row has a date.
    """
    for key in ('season_week', 'week'):
        v = ctx.get(key)
        if v is None:
            continue
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv > 0:
            return iv
    # No league week exists for this sport. Derive a stable grouping key
    # from the date so the cadence still runs.
    try:
        d = game_date
        if isinstance(d, str):
            d = date.fromisoformat(str(d)[:10])
        if isinstance(d, datetime):
            d = d.date()
        return int(d.isocalendar()[1])
    except (TypeError, ValueError, AttributeError):
        return None


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


# ══ 2026-10-28 · NOTHING WROTE NBA OR NHL ATTRIBUTION ══
# This map held three sports, and --sport took its choices from it, so NBA and
# NHL could not be snapshotted even by hand. The consequence runs all the way
# down the chain Andy set as the standard on 10-27:
#
#   signal_attribution rows   NCAAF 3,101   NFL 985   MLB 351   NHL 197   NBA 74
#
# No snapshots -> no attribution -> refit_signal_registry can never compute a
# weight -> every NBA game signal sits at UNVALIDATED with sample_n=0 and the
# 0.3 prior. Measured today: NOT ONE NBA game signal has sample_n > 0, and the
# four NBA signals carrying real weight (1.0) are all PROP signals, which
# cannot fire on a game pick at all. So NBA game picks are the sum of a
# handful of evidence-free priors.
_TBL_BY_SPORT = {
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'MLB':   'mlb_game_context',
    'NBA':   'nba_game_context',
    'NHL':   'nhl_game_context',
    'NCAAB': 'ncaab_game_context',
}


def _fetch_games(sport: str, game_date: str, days: int = 1) -> list[dict]:
    tbl = _TBL_BY_SPORT.get(sport)
    if not tbl: return []
    # 2026-09-13 sport-specific offense-rating column:
    #   NFL   → home_off_rating (only exists on NFL ctx)
    #   NCAAF → home_off_epa_pp (only exists on NCAAF ctx)
    #   MLB   → no offense-rating field on ctx (uses splits_summary /
    #           cohort v2 elsewhere); slim column set
    # Aliasing to a common name (`home_off_rating`) via PostgREST select
    # so build_signal_rows can read one canonical field regardless of sport.
    if sport == 'NFL':
        cols = ('game_id,season,week,season_week,game_date,'
                'home_team,away_team,cohort_tags,align_status,'
                'primary_play,spread_anchor_weight,'
                'home_off_rating,away_off_rating')
    elif sport == 'NCAAF':
        cols = ('game_id,season,week,season_week,game_date,'
                'home_team,away_team,cohort_tags,align_status,'
                'primary_play,spread_anchor_weight,'
                'home_off_rating:home_off_epa_pp,'
                'away_off_rating:away_off_epa_pp')
    elif sport in ('NBA', 'NCAAB'):
        # Measured 2026-10-28: nba_game_context has season, align_status and
        # home/away_off_rating but NOT week, season_week, cohort_tags or
        # spread_anchor_weight. Asking for a column that is not there 400s the
        # whole select and returns zero games, which would look like "no games
        # today" (feedback_explicit_select_silent_blanks), so the set is
        # explicit per sport rather than shared and hopeful.
        cols = ('game_id,season,game_date,home_team,away_team,'
                'align_status,primary_play,close_spread,close_total,'
                'home_off_rating,away_off_rating,splits_summary')
    elif sport == 'NHL':
        # NHL has no off_rating pair at all, and its spread column is
        # close_puckline. Aliased to close_spread so the downstream anchor
        # code reads one canonical name.
        cols = ('game_id,season,game_date,home_team,away_team,'
                'align_status,primary_play,close_total,splits_summary,'
                'close_spread:close_puckline')
    else:  # MLB — no cohort_tags/spread_anchor_weight/off_rating on ctx.
        # Season derived from game_date year on writer side; week is N/A.
        # splits_summary + primary_play carry everything we need.
        cols = ('game_id,game_date,home_team,away_team,'
                'primary_play,align_status,splits_summary')
    # 2026-09-17: expanded from today-only to days-window. Prior single-date
    # filter silently returned 0 games on Tue/Wed/Thu (no games scheduled)
    # → signal_attribution stayed empty for NFL/NCAAF weekend games.
    from datetime import datetime as _dt, timedelta as _td
    end_date = (_dt.strptime(game_date,'%Y-%m-%d') + _td(days=days-1)).strftime('%Y-%m-%d')
    url = f'{SB}/rest/v1/{tbl}?game_date=gte.{game_date}&game_date=lte.{end_date}&select={cols}'
    r = requests.get(url,
                     headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-499'},
                     timeout=20)
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


#: Which side the closing number favours, per sport. DERIVED, not assumed:
#: tested against the stored result on every graded spread row, NCAAF
#: 694/774 and NFL 225/225 reproduce it (see scratch sig_orient.py).
#: NCAAF close_line is the HOME HANDICAP (positive = home getting points);
#: NFL close_line is positive when HOME IS FAVOURED. Getting this backwards
#: would label every anchored favourite as a dog, so a sport with no measured
#: convention returns NEUTRAL rather than a guess.
#
#: 2026-10-28 · the remaining four sports, measured the same way: correlate
#: the stored close number against the realised home margin, and check how
#: often home wins when that number is NEGATIVE.
#:
#:   sport      n     corr    home win% when negative
#:   NBA     2,449   -0.517      68.8%   -> negative = home favoured, -1.0
#:   NHL     2,718   -0.172      60.1%   -> negative = home favoured, -1.0
#:   MLB     2,136   -0.133      58.2%   -> negative = home favoured, -1.0
#:   NCAAB   5,027   -0.644      75.9%   -> negative = home favoured, -1.0
#:   NCAAF   6,618   -0.721      78.7%   (already here)
#:   NFL     7,383   +0.425      35.3%   (already here; NFL is the odd one)
#:
#: NFL stays +1.0 and is the only sport that way -- home wins just 35.3% of
#: the games where its number is negative, which is the convention inverted.
_CLOSE_LINE_HOME_FAV_SIGN = {'NCAAF': -1.0, 'NFL': 1.0,
                             'NBA': -1.0, 'NHL': -1.0,
                             'MLB': -1.0, 'NCAAB': -1.0}


def _anchor_subject_side(sport, ctx):
    """HOME/AWAY that the close favours, or NEUTRAL when undeterminable."""
    sgn = _CLOSE_LINE_HOME_FAV_SIGN.get(str(sport or '').upper())
    if sgn is None:
        return 'NEUTRAL'
    for col in ('close_spread', 'close_line'):
        try:
            v = float(ctx.get(col))
        except (TypeError, ValueError):
            continue
        if abs(v) < 0.25:
            return 'NEUTRAL'          # a pick'em has no favourite
        return 'HOME' if sgn * v > 0 else 'AWAY'
    return 'NEUTRAL'


def _build_rows(sport: str, ctx: dict) -> list[dict]:
    """Extract fired signals for one game → list of snapshot rows."""
    pp = ctx.get('primary_play') or {}
    if not isinstance(pp, dict):
        return []

    game_id = ctx.get('game_id')
    game_date = ctx.get('game_date')
    if not (game_id and game_date):
        return []

    pick_market = pp.get('type')
    pick_side = (pp.get('side') or '').upper() or None
    pick_tier = str(pp.get('tier') or '').upper() or None
    is_ml_or_spread = pick_market in ('ml', 'spread', 'rl')

    base = {
        'sport': sport,
        'game_id': game_id,
        'game_date': game_date,
        'season': ctx.get('season'),
        'season_week': resolve_week(ctx, game_date),
        'pick_market': pick_market,
        'pick_side': pick_side,
        'pick_tier': pick_tier,
    }
    out: list[dict] = []

    # OFF_RATING_GAP (was EPA_GAP; renamed for column-name accuracy —
    # home_off_rating is the aggregate offense signal both sports carry.
    # Real EPA-per-game / EPA-per-play cols exist per sport but under
    # different names — this is the cleanest single field).
    he = ctx.get('home_off_rating')
    ae = ctx.get('away_off_rating')
    try:
        if he is not None and ae is not None:
            gap = float(he) - float(ae)
            # Threshold: 0.5 rating pts is meaningful on the ~50-120 scale
            if abs(gap) >= 0.5:
                leader = 'HOME' if gap > 0 else 'AWAY'
                kind = ('ok' if (is_ml_or_spread and leader == pick_side)
                        else 'warn' if is_ml_or_spread
                        else 'neutral')
                out.append({**base, 'signal_key': 'OFF_RATING_GAP',
                            'signal_value': round(gap, 2),
                            'signal_side': leader, 'kind': kind})
    except (TypeError, ValueError): pass

    # LR_SHADOW (from primary_play._lr_ml_shadow)
    lr = pp.get('_lr_ml_shadow') or {}
    if isinstance(lr, dict):
        try:
            p = float(lr.get('p_home_win'))
            lr_side = ('HOME' if p >= 0.55 else 'AWAY' if p < 0.45 else 'NEUTRAL')
            if lr_side != 'NEUTRAL':
                kind = ('ok' if (is_ml_or_spread and lr_side == pick_side)
                        else 'warn' if is_ml_or_spread
                        else 'neutral')
                out.append({**base, 'signal_key': 'LR_SHADOW',
                            'signal_value': round(p, 4),
                            'signal_side': lr_side, 'kind': kind})
        except (TypeError, ValueError): pass

    # LR_TOTAL_SHADOW (from primary_play._lr_total_shadow) — MLB-heavy but
    # also fires on NCAAF/NFL when total LR models are populated.
    lrt = pp.get('_lr_total_shadow') or {}
    if isinstance(lrt, dict) and pick_market == 'total':
        try:
            po = float(lrt.get('p_over'))
            lrt_side = ('OVER' if po >= 0.55 else 'UNDER' if po < 0.45 else 'NEUTRAL')
            if lrt_side != 'NEUTRAL':
                kind = 'ok' if lrt_side == pick_side else 'warn'
                out.append({**base, 'signal_key': 'LR_TOTAL_SHADOW',
                            'signal_value': round(po, 4),
                            'signal_side': lrt_side, 'kind': kind})
        except (TypeError, ValueError): pass

    # SPLITS_SHARP (MLB — from ctx.splits_summary sharp side, when confident).
    # Only fires when a sharp side is present + the magnitude is meaningful.
    ss = ctx.get('splits_summary') or {}
    if isinstance(ss, dict):
        try:
            sharp = str(ss.get('sharp_side') or '').upper()
            magnitude = float(ss.get('sharp_money_pct') or 0)
            if sharp in ('HOME', 'AWAY', 'OVER', 'UNDER') and magnitude >= 60:
                kind = ('ok' if (pick_side and sharp == pick_side)
                        else 'warn' if pick_side else 'neutral')
                out.append({**base, 'signal_key': 'SPLITS_SHARP',
                            'signal_value': round(magnitude, 1),
                            'signal_side': sharp, 'kind': kind})
        except (TypeError, ValueError): pass

    # ANCHOR
    aw = ctx.get('spread_anchor_weight')
    try:
        if aw is not None and float(aw) > 0:
            # ANCHOR is a deference WEIGHT, not a pick, so its subject is
            # the side the market favours -- that is what the projection is
            # being pulled toward. Recording it makes "when we defer hard to
            # the market, does the market side cover?" measurable, which is
            # the only question that can justify or condemn the anchor.
            # close_line orientation is per-sport and MEASURED, not assumed
            # (NCAAF 694/774, NFL 225/225): see _anchor_subject_side.
            out.append({**base, 'signal_key': 'ANCHOR',
                        'signal_value': round(float(aw), 2),
                        'signal_side': _anchor_subject_side(
                            base.get('sport'), ctx),
                        'kind': 'neutral'})
    except (TypeError, ValueError): pass

    # GOAT — from align_status.chips_extra
    als = ctx.get('align_status') or {}
    if isinstance(als, dict):
        for chip in (als.get('chips_extra') or []):
            if isinstance(chip, dict) and chip.get('key') == 'goat':
                val = chip.get('value', '')
                if val and val != 'PASS':
                    # Format: "PIT · LEAN" — first token is side
                    tok = str(val).split('·')[0].strip().upper()
                    kind = ('ok' if (is_ml_or_spread and tok == pick_side)
                            else 'neutral')
                    out.append({**base, 'signal_key': 'GOAT',
                                'signal_value': None,
                                'signal_side': tok if tok in ('HOME','AWAY') else 'NEUTRAL',
                                'kind': kind})

    # Cohort tags
    for tag in (ctx.get('cohort_tags') or []):
        if not isinstance(tag, str): continue
        key = f'COHORT_{tag.upper().replace(" ", "_")}'
        # ══ A SIGNAL WITH NO RECORDED SIDE CAN NEVER BE CALIBRATED ══
        # 2026-10-24. Every COHORT_* row was written with signal_side
        # 'NEUTRAL', and so was ANCHOR -- 931 of 2,034 rows, 46%. The
        # consequence is not cosmetic: "did the side this signal argued
        # cover?" is unanswerable, so the only number available for these
        # signals is OUR PICK's result on games where they fired. That is a
        # measurement of our own selection, not of the signal, and it is what
        # ANCHOR's "46.2%" and HEAVY_HOME_DOG's "40.6%" actually were. I
        # recommended retiring both on those figures. Andy: "why retire
        # automatically intesad of looking liek whats driving those negative
        # ROI". He was right -- there was nothing to look at.
        #
        # The cohort tag already names its SUBJECT, so the side is derivable
        # with no new data: every side-bearing tag on the board is
        # home-referenced (HOME_FAV, HEAVY_HOME_FAV, HEAVY_HOME_DOG,
        # DIV_HOME_COVER). SHOOTOUT and GRINDER describe the TOTAL and name
        # no side, so they stay NEUTRAL rather than being given a fake one.
        #
        # Recording the subject is deliberately NOT the same as recording a
        # recommendation: whether to follow or fade the subject is then a
        # calibration OUTPUT instead of an assumption baked into the writer
        # (feedback_signal_needs_a_direction_control).
        _subj = 'HOME' if 'HOME' in key else 'NEUTRAL'
        out.append({**base, 'signal_key': key, 'signal_value': None,
                    'signal_side': _subj, 'kind': 'ok'})

    return out


def upsert_batch(rows: list[dict]) -> int:
    if not rows: return 0
    r = requests.post(
        f'{SB}/rest/v1/signal_attribution?on_conflict=sport,game_id,signal_key',
        headers=H_WRITE, json=rows, timeout=20,
    )
    if r.status_code in (200, 201, 204):
        return len(rows)
    print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
    return 0


def run(sport: Optional[str] = None,
        game_date: Optional[str] = None,
        dry_run: bool = False,
        days: int = 14) -> None:
    gd = game_date or _today_et()
    sports = [sport] if sport else ['NFL', 'NCAAF', 'MLB']
    print(f'=== signal_attribution_snapshot · {gd}+{days-1}d · sports={sports} '
          f'{"(DRY)" if dry_run else "(APPLY)"} ===')
    for sp in sports:
        games = _fetch_games(sp, gd, days=days)
        print(f'  {sp}: {len(games)} games in ctx')
        all_rows: list[dict] = []
        for ctx in games:
            if isinstance(ctx, dict):
                all_rows.extend(_build_rows(sp, ctx))
        print(f'  {sp}: {len(all_rows)} signal rows to snapshot')
        if dry_run:
            for r in all_rows[:8]:
                print(f'    [DRY] {r["signal_key"]:20s} side={r["signal_side"]} '
                      f'kind={r["kind"]} · game={r["game_id"][:20]}')
        else:
            written = upsert_batch(all_rows)
            print(f'  {sp}: wrote/merged {written} rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=sorted(_TBL_BY_SPORT))
    p.add_argument('--date', dest='game_date')
    p.add_argument('--days', type=int, default=14,
                   help='window size in days from --date (default 14)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(sport=args.sport, game_date=args.game_date, dry_run=args.dry_run, days=args.days)


if __name__ == '__main__':
    main()
