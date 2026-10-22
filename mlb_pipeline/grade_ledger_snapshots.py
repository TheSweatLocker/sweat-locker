"""Grade ledger snapshots (2026-08-18).

Runs after all sports have resolved (nightly). For each ungraded
ledger_snapshot, look up per-leg results:
  - ML leg: home_score vs away_score (from sport's game_results table)
  - RL/spread leg: home_score - away_score vs spread (teased line if teased)
  - Total leg: home_score + away_score vs total (teased line if teased)

Combo wins if ALL legs win. Any leg push → drop that leg (recompute odds).
Any leg loss → whole combo loses. Compute unit_pnl at 1u stake.

Idempotent: only regrades snapshots where result IS NULL.
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone, timedelta
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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

SPORT_RESULTS = {
    'MLB':   'mlb_game_results',
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
    'NHL':   'nhl_game_results',
    'NBA':   'nba_game_results',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _american_to_decimal(o: float) -> float:
    return 1 + (o / 100 if o >= 0 else 100 / abs(o))


def _combined_odds(decs: list[float]) -> int:
    prod = 1.0
    for d in decs: prod *= d
    payout = prod - 1
    return int(round(100 * payout)) if payout >= 1 else int(round(-100 / payout))


def _fetch_result(sport: str, game_id: str) -> dict | None:
    table = SPORT_RESULTS.get(sport)
    if not table: return None
    r = requests.get(f'{SB}/rest/v1/{table}',
                     headers=H_READ,
                     params={'game_id': f'eq.{game_id}',
                             'select': 'game_id,home_score,away_score,home_team,away_team',
                             'limit': '1'},
                     timeout=10)
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None


def _resolve_pick_side(leg: dict, result: dict) -> str | None:
    """Return 'HOME' or 'AWAY' for the pick, or None if unresolvable.

    2026-08-22: prior grader defaulted to 'AWAY' whenever `leg.side` was
    missing AND 'home' wasn't literally in the pick label ('Philadelphia
    Phillies ML' contains neither 'home' nor 'HOME'). Every chalk_parlay
    leg had no `side` field so ALL ML picks were graded as AWAY picks —
    home wins showed as leg losses, home losses showed as leg wins.

    Fix: prefer explicit leg.side; fall back to matching the team name
    embedded in leg.pick against result.home_team / result.away_team.
    """
    side = (leg.get('side') or '').upper()
    if side in ('HOME', 'AWAY'): return side
    pick_label = (leg.get('pick') or '').strip()
    if not pick_label: return None
    # Strip trailing " ML" / " RL" / " +/-N.N" / " Over/Under N.N" tokens
    import re
    label_clean = re.sub(r'\s+(ML|RL|[+-]?\d+(\.\d+)?|Over|Under)\s*$', '', pick_label, flags=re.I).strip()
    home_team = str(result.get('home_team') or '').strip()
    away_team = str(result.get('away_team') or '').strip()
    if home_team and label_clean.lower() in home_team.lower(): return 'HOME'
    if away_team and label_clean.lower() in away_team.lower(): return 'AWAY'
    if home_team and home_team.lower() in label_clean.lower(): return 'HOME'
    if away_team and away_team.lower() in label_clean.lower(): return 'AWAY'
    return None


# Prop market → stat key (MLB Stats API). Mirrors grade_props.STAT_MAP_MLB.
_PROP_MARKET_STAT = {
    'prop_hits_over': 'h_bat', 'prop_hits_under': 'h_bat',
    'prop_ks_over':   'ks',    'prop_ks_under':   'ks',
    'prop_bb_over':   'bb',    'prop_bb_under':   'bb',
    'prop_er_over':   'er',    'prop_er_under':   'er',
    'prop_ha_over':   'h_pit', 'prop_ha_under':   'h_pit',
    'prop_outs_over': 'outs',  'prop_outs_under': 'outs',
}


#: Suffixes the boxscore and the odds feed disagree about. The MLB Stats API
#: carries 'Rafael Flores Jr'; the props row that produced the ledger leg
#: says 'Rafael Flores'. Stripping them is only safe behind a UNIQUENESS
#: check, because a father and son can both be in the league.
_NAME_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}


def _strip_suffix(key: str) -> str:
    parts = [w for w in key.split() if w]
    while len(parts) > 2 and parts[-1] in _NAME_SUFFIXES:
        parts.pop()
    return ' '.join(parts)


def _lookup_player_stats(player_stats: dict, player_name: str):
    """Find a player in the boxscore map without joining on a raw name.

    Three steps, each stricter than a substring match:

      1. grade_props._norm_name on BOTH sides. That function already folds
         accents, drops periods, apostrophes and the odds feed's '(2002)'
         disambiguator - it was written for exactly this mismatch on
         2026-09-23 and this module simply never used it. 'Victor Mesa Jr.'
         -> 'victor mesa jr' matches the boxscore key that `.lower()` missed
         by one period.
      2. generational suffix stripped from both sides, accepted ONLY when it
         resolves to a single candidate. 'Rafael Flores' finds 'rafael
         flores jr' this way; a 'Smith Jr' with two Smiths in the map finds
         nothing and falls through, which is the outcome we want.
      3. give up. An unmatched name returns None and the leg grades 'NR',
         which leaves the slip ungraded rather than guessing.

    Never matches on last name alone. A wrong player grades a real bet the
    wrong way round, and that is worse than an ungraded slip.
    """
    if not player_stats or not player_name:
        return None
    try:
        from grade_props import _norm_name
    except Exception:
        _norm_name = lambda n: ' '.join(str(n or '').replace('.', '')
                                        .replace("'", '').lower().split())
    want = _norm_name(player_name)
    if not want:
        return None
    normed = {}
    for k, v in player_stats.items():
        normed.setdefault(_norm_name(k), v)
    hit = normed.get(want)
    if hit is not None:
        return hit
    want_bare = _strip_suffix(want)
    cands = [v for k, v in normed.items() if _strip_suffix(k) == want_bare]
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        print(f'    {player_name}: {len(cands)} boxscore names collapse to '
              f'"{want_bare}" - refusing to guess', file=sys.stderr)
    return None

# ── grading a prop leg from the row it came FROM (2026-10-21) ─────────────
# Ledger prop legs are built by generate_ledger.fetch_picks, which reads the
# per-sport *_pipeline_props tables. Those tables grade themselves nightly.
# So the leg and its result live in the same row, and the honest way to grade
# the leg is to go back and read it - not to re-derive the outcome from a
# second source.
#
# The old path did the latter, and it had two holes that between them left
# every prop parlay since 2026-09-10 ungraded:
#
#   1. It was MLB-ONLY. _PROP_MARKET_STAT has six baseball keys and the
#      player map comes from statsapi.mlb.com, so an NFL leg returned 'NR'
#      forever - and 'NR' makes the whole combo 'NR', which is `continue`d
#      without a write. Slip 191 (two Seahawks/Patriots receiving props) has
#      sat at result=NULL since 09-10 for exactly this reason. NBA and NHL
#      prop legs would do the same.
#   2. It matched players by `player_name.lower()` against the boxscore's
#      spelling - a RAW-NAME JOIN across two feeds. Measured on slip 239:
#      'Victor Mesa Jr.' lowercased keeps its period and the boxscore key is
#      'victor mesa jr'; 'Rafael Flores' has no suffix at all where the
#      boxscore says 'rafael flores jr'. Both missed, both returned 'NR'.
#      Same defect as the STL name mismatch and the UFC fight_order grading.
#
# Reading the props row removes both: it is an identity join on the fields
# the leg was copied from, it needs no name normalization, and it covers
# every sport that has a props table. It also inherits the pipeline's own
# 'Void' - a DNP'd player - which the stat path could only see as a miss.
_PROP_RESULT_TABLE = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',
    'NHL': 'nhl_pipeline_props',
    'NBA': 'nba_pipeline_props',
}

#: Void is a leg the book takes OFF the slip (scratched, DNP), so it behaves
#: as a push: it stops being a loss and the combo re-prices without it.
#: Treating it as 'L' would charge us for a bet that was never live.
_PROP_RESULT_CODE = {
    'win': 'W', 'w': 'W',
    'loss': 'L', 'l': 'L', 'lose': 'L',
    'push': 'P', 'p': 'P', 'void': 'P',
}


def _grade_prop_leg_from_table(leg: dict) -> str | None:
    """The leg's own props row, or None if there is no single matching row.

    Returns None - not 'NR' - when the lookup is inconclusive, so the caller
    can still try the stat path. Only an unambiguous single row with a
    recognised result decides the leg; two rows matching means the key did
    not identify one bet, and guessing between them is how a win gets booked
    as a loss.
    """
    sport = str(leg.get('sport') or '').upper()
    table = _PROP_RESULT_TABLE.get(sport)
    gid = leg.get('game_id')
    name = leg.get('player_name')
    ptype = leg.get('prop_type')
    line = leg.get('teased_line')
    if line is None:
        line = leg.get('original_line')
    if not (table and gid and name and ptype) or line is None:
        return None
    try:
        r = requests.get(
            f'{SB}/rest/v1/{table}',
            headers=H_READ,
            params={'game_id': f'eq.{gid}',
                    'player_name': f'eq.{name}',
                    'prop_type': f'eq.{ptype}',
                    'prop_line': f'eq.{float(line)}',
                    'select': 'result,player_name,prop_type,prop_line'},
            timeout=15)
        if r.status_code != 200:
            print(f'    prop lookup {table} HTTP {r.status_code}: '
                  f'{r.text[:160]}', file=sys.stderr)
            return None
        rows = r.json() or []
    except Exception as e:
        print(f'    prop lookup {table} failed: {type(e).__name__}: {e}',
              file=sys.stderr)
        return None
    codes = {_PROP_RESULT_CODE.get(str(x.get('result') or '').strip().lower())
             for x in rows}
    codes.discard(None)
    if len(rows) > 1 and len(codes) > 1:
        # The same player/type/line graded two ways. Do not pick one.
        print(f'    AMBIGUOUS: {name} {ptype} {line} has {len(rows)} rows '
              f'with results {sorted(codes)} - not grading from table',
              file=sys.stderr)
        return None
    return next(iter(codes)) if len(codes) == 1 else None


def _grade_prop_leg(leg: dict, player_stats: dict | None) -> str:
    """Grade a prop leg using MLB Stats API player stats.

    player_stats: {player_name_lower: {ks, bb, er, h_pit, outs, h_bat}}
    Returns 'W', 'L', 'P', or 'NR'.

    2026-10-21: the leg's own props row is consulted FIRST (see
    _grade_prop_leg_from_table). The MLB stat path below stays as the
    fallback for a leg whose props row was deleted or re-keyed, and it is
    still the only path that can grade a leg with no prop_type.
    """
    from_table = _grade_prop_leg_from_table(leg)
    if from_table:
        return from_table
    if not player_stats: return 'NR'
    market = str(leg.get('market') or '').lower()
    # 2026-09-11 chalk_prop_parlay writes market='prop' with detailed
    # `prop_type` (e.g., 'outs_under'). Fall back to prop_type-prefixed
    # lookup when raw market isn't in the map. Same-map, two entry points.
    stat_key = _PROP_MARKET_STAT.get(market)
    if not stat_key and leg.get('prop_type'):
        stat_key = _PROP_MARKET_STAT.get(f"prop_{str(leg.get('prop_type')).lower()}")
    if not stat_key: return 'NR'
    # Prefer explicit player_name; fall back to parsing 'pick' string
    # ("Miguel Vargas Over 0.5 Hits" → "Miguel Vargas")
    player_name = (leg.get('player_name') or '').strip()
    if not player_name:
        pick = str(leg.get('pick') or '')
        for kw in (' Over ', ' Under '):
            if kw in pick:
                player_name = pick.split(kw, 1)[0].strip()
                break
    if not player_name: return 'NR'
    stats = _lookup_player_stats(player_stats, player_name)
    if not stats: return 'NR'
    actual = stats.get(stat_key)
    if actual is None: return 'NR'
    line = leg.get('teased_line') if leg.get('teased_line') is not None else leg.get('original_line')
    if line is None: return 'NR'
    line = float(line)
    # Direction: side field wins, else parse from pick, else from market suffix
    side = (leg.get('side') or '').upper()
    if side not in ('OVER', 'UNDER'):
        pick_up = str(leg.get('pick') or '').upper()
        if 'OVER' in pick_up: side = 'OVER'
        elif 'UNDER' in pick_up: side = 'UNDER'
        elif market.endswith('_over'):  side = 'OVER'
        elif market.endswith('_under'): side = 'UNDER'
        else: return 'NR'
    if side == 'OVER':
        if actual > line: return 'W'
        if actual < line: return 'L'
        return 'P'
    if actual < line: return 'W'
    if actual > line: return 'L'
    return 'P'


def _grade_leg(leg: dict, result: dict, player_stats: dict | None = None) -> str:
    """Return 'W', 'L', 'P', or 'NR' (not resolved)."""
    market = str(leg.get('market') or '').lower()
    # 2026-09-11 chalk_prop_parlay writes market='prop' with prop_type
    # populated. Route bare 'prop' + any leg carrying prop_type through
    # the prop grader too (was skipping and returning NR unconditionally).
    if market == 'prop' or market.startswith('prop_') or leg.get('prop_type'):
        return _grade_prop_leg(leg, player_stats)
    hs = result.get('home_score') if result else None
    as_ = result.get('away_score') if result else None
    if hs is None or as_ is None: return 'NR'
    market = str(leg.get('market') or '').lower()
    # Use teased line if present, else original_line
    line = leg.get('teased_line') if leg.get('teased_line') is not None else leg.get('original_line')
    if market == 'ml':
        pick_side = _resolve_pick_side(leg, result)
        if pick_side is None: return 'NR'
        actual = 'HOME' if hs > as_ else 'AWAY' if as_ > hs else 'PUSH'
        if actual == 'PUSH': return 'P'
        return 'W' if pick_side == actual else 'L'
    if market in ('rl', 'spread', 'runline'):
        if line is None: return 'NR'
        pick_side = _resolve_pick_side(leg, result)
        if pick_side is None: return 'NR'
        # 2026-08-22 FIX: line is stored PICK-perspective (+3 means the
        # pick side gets +3), not HOME-perspective. Prior code assumed
        # home-perspective and produced opposite-sign results for AWAY
        # picks with positive lines — Pirates +3 covering easily was
        # graded L. Now: line_home = line if pick=HOME else -line.
        line_home = float(line) if pick_side == 'HOME' else -float(line)
        margin_home = hs - as_
        cover = margin_home + line_home
        if abs(cover) < 0.01: return 'P'
        if pick_side == 'HOME': return 'W' if cover > 0 else 'L'
        else:                    return 'W' if cover < 0 else 'L'
    if market == 'total':
        if line is None: return 'NR'
        side_label = (leg.get('side') or '').upper()
        pick_label = (leg.get('pick') or '').upper()
        pick_side = side_label if side_label in ('OVER', 'UNDER') \
                    else ('OVER' if 'OVER' in pick_label else 'UNDER' if 'UNDER' in pick_label else None)
        if pick_side is None: return 'NR'
        actual = hs + as_
        if abs(actual - float(line)) < 0.01: return 'P'
        return ('W' if actual > float(line) else 'L') if pick_side == 'OVER' \
               else ('W' if actual < float(line) else 'L')
    return 'NR'


def grade_date(gd: str, dry_run: bool = False) -> tuple[int, int]:
    r = requests.get(f'{SB}/rest/v1/ledger_snapshots',
                     headers=H_READ,
                     params={'game_date': f'eq.{gd}', 'result': 'is.null',
                             'select': '*'},
                     timeout=20)
    snaps = r.json() if r.status_code == 200 else []
    if not snaps: return 0, 0

    # 2026-08-30: lazy-load MLB batter/pitcher stats once per date if any
    # snapshot has a prop leg. Feeds _grade_prop_leg for hits_parlay etc.
    # 2026-09-11: chalk_prop_parlay legs write market='prop' (not 'prop_*'),
    # so the startswith check was missing them → 11 rows ungraded over
    # 9/9-9/10. Include bare 'prop' + presence of prop_type field.
    player_stats = None
    def _is_prop_leg(l):
        m = str(l.get('market') or '').lower()
        return m == 'prop' or m.startswith('prop_') or bool(l.get('prop_type'))
    needs_props = any(_is_prop_leg(l) for s in snaps for l in (s.get('legs') or []))
    if needs_props:
        # 2026-10-21: this unpacked TWO values. fetch_player_stats_for_date
        # started returning a THIRD (postponed_matchups) on 2026-09-23, so
        # every call since has raised ValueError before touching the network,
        # and the except below turned it into a one-line warning. Any ledger
        # slip carrying a prop leg has been UNGRADEABLE for a month, sitting
        # at result=NULL and counted in no record. Two slips (9/10, 9/11) are
        # still stuck on it.
        #
        # Unpacking by * means the next field added here is absorbed instead
        # of crashing, and the error below now names its TYPE so a signature
        # drift never again reads like a flaky feed.
        try:
            from grade_props import fetch_player_stats_for_date
            player_stats, _n_games, *_rest = fetch_player_stats_for_date(gd)
            print(f'  loaded MLB player stats: {len(player_stats)} players from {_n_games} games')
        except Exception as e:
            print(f'  ⚠ player stats fetch failed: {type(e).__name__}: {e}')
            if isinstance(e, (ValueError, TypeError, ImportError, AttributeError)):
                print('    ^ that is a CODE error, not a feed error - prop legs '
                      'will not grade until it is fixed', file=sys.stderr)

    graded = skipped = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for snap in snaps:
        legs = snap.get('legs') or []
        results_per_leg = []
        decs_after_pushes = []
        combo_status = 'W'  # assume win, downgrade on any L/NR
        legs_hit = legs_pushed = 0
        for leg in legs:
            sport = leg.get('sport')
            gid = leg.get('game_id')
            if not sport or not gid:
                combo_status = 'NR'; break
            market = str(leg.get('market') or '').lower()
            # Prop legs bypass sport-results fetch — graded via player stats.
            # 2026-09-11 accept bare 'prop' too (chalk_prop_parlay uses that).
            if market == 'prop' or market.startswith('prop_') or leg.get('prop_type'):
                outcome = _grade_leg(leg, {}, player_stats=player_stats)
            else:
                res = _fetch_result(sport, gid)
                if not res:
                    combo_status = 'NR'; break
                outcome = _grade_leg(leg, res, player_stats=player_stats)
            results_per_leg.append(outcome)
            if outcome == 'W':
                legs_hit += 1
                odds = leg.get('teased_odds') if leg.get('teased_odds') is not None else leg.get('original_odds')
                if odds is not None:
                    try: decs_after_pushes.append(_american_to_decimal(float(odds)))
                    except (TypeError, ValueError): pass
            elif outcome == 'P':
                legs_pushed += 1
            elif outcome == 'L':
                combo_status = 'L'
            elif outcome == 'NR':
                combo_status = 'NR'

        if combo_status == 'NR':
            skipped += 1
            continue
        # Recompute odds after pushes (dropped legs)
        if combo_status == 'W' and decs_after_pushes:
            new_combined = _combined_odds(decs_after_pushes)
            pnl = new_combined / 100 if new_combined >= 0 else 100 / abs(new_combined)
        elif combo_status == 'L':
            pnl = -1.0
        else:
            pnl = 0.0

        payload = {
            'result': combo_status,
            'legs_hit': legs_hit,
            'legs_pushed': legs_pushed,
            'graded_at': now_iso,
            'unit_pnl': round(pnl, 3),
        }
        if dry_run:
            graded += 1
            continue
        pr = requests.patch(
            f'{SB}/rest/v1/ledger_snapshots?id=eq.{snap["id"]}',
            headers=H_WRITE, json=payload, timeout=10,
        )
        if pr.status_code in (200, 204):
            graded += 1
    return graded, skipped


def run(game_date: str = None, days: int = 1, dry_run: bool = False):
    if game_date:
        dates = [game_date]
    else:
        dates = [(date.today() - timedelta(days=d+1)).isoformat() for d in range(days)]
    total_g = total_s = 0
    for gd in dates:
        g, s = grade_date(gd, dry_run=dry_run)
        total_g += g; total_s += s
        print(f'  {gd}: graded={g} skipped={s}')
    print(f'\n  {"[DRY] " if dry_run else ""}total: graded={total_g} skipped={total_s}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: yesterday ET)')
    p.add_argument('--days', type=int, default=7, help='backfill last N days')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
