"""NFL play-of-day generator — reads nfl_game_context, tier-gates picks,
writes to nfl_game_picks, flags lock_of_week.

Runs AFTER nfl_game_context.py in the cron. Because game_context already
computes projections + confluence + primary_play, this file is thin:
   1. Read upcoming-week rows from nfl_game_context
   2. For each row with primary_play, write a nfl_game_picks row
   3. Select ONE pick per week as lock_of_week (highest conviction, tie-break
      prefers PRIME → STRONG → LIGHT; within tier prefers cohort_tags with
      audit-validated hit rates)
   4. Also write "skip" rows for high-visibility chalk traps (helps app
      surface Skip Alerts consistently)

Weekly-cadence mental model:
  Tue 11am ET cron → context refresh + picks write
  Thu 2pm ET cron  → lock_of_week finalized after TNF landscape known
  Sun 8am ET cron  → last refresh before Sunday slate goes live

Result column filled post-game by resolve_nfl_results.py (Phase 3.2).

Usage:
    python nfl_play_of_day.py             # process upcoming games
    python nfl_play_of_day.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, date, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# Cohort priority order for lock-of-week tie-breaking. Audit-validated
# cohorts first, then confluence-derived, then generic.
COHORT_PRIORITY = [
    'nfl_heavy_home_dog',    # 63.1% audit lifetime (n=65)
    'nfl_outdoor_under',     # 55.6% audit lifetime (n=196)
    'nfl_dome_over',
    'nfl_div_home_cover',
    'nfl_home_fav',
]

# Skip-alert cohorts (chalk traps — write as pick_type='skip')
SKIP_COHORTS = {
    # Mid-range div home fav (-3.5 to -6.5): 48.6% audit — coinflip trap
    'div_home_fav_mid': lambda ctx: (
        ctx.get('close_spread') is not None
        and 3.5 <= float(ctx['close_spread']) <= 6.5
        and ctx.get('div_game') is True
    ),
    # Dome + total >= 50 (juiced OVER)
    'dome_over_juiced': lambda ctx: (
        (ctx.get('roof') or '').lower() in ('dome', 'closed')
        and ctx.get('close_total') is not None
        and float(ctx['close_total']) >= 50.0
    ),
}


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def load_upcoming(days_ahead: int = 10, since: Optional[str] = None) -> list:
    """Pull nfl_game_context rows whose kickoff is today or within N days.

    `since` overrides the start date to backfill games already played.
    Used to repair the 9/09-9/22 ledger gap. It runs through the same
    build_pick_row path as the live cron on purpose — a separate
    backfill script would be free to drift from the real selection
    logic, which is how a 'record' stops meaning anything.
    """
    today = _et_now().date()
    if since:
        today = datetime.fromisoformat(since).date()
    horizon = _et_now().date() + timedelta(days=days_ahead)
    r = requests.get(
        f'{SB}/rest/v1/nfl_game_context'
        f'?game_date=gte.{today.isoformat()}'
        f'&game_date=lte.{horizon.isoformat()}'
        f'&select=*&order=game_date.asc',
        headers=H_READ, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def _side_from_play(play: dict, ctx: dict) -> Optional[str]:
    """Convert primary_play.label → structured pick_side.

    primary_play is generated in nfl_game_context.compute_primary_play with
    labels like "Panthers +7", "Over 44.5", "PHI spread lean". We reconstruct
    'home' | 'away' | 'over' | 'under'.
    """
    if not play: return None

    # 2026-09-22: TRUST `side` OVER `label`. These two can disagree — a
    # mutator that flips side without rewriting the label leaves a stale
    # team name behind (NYG @ LA, 2026-09-21: side=AWAY, label='LA -7').
    # Deriving pick_side from the label copied that bug straight into the
    # graded ledger, recording a pick on LA that the ensemble never made.
    # `side` is what the scorer actually chose and what the week lock
    # freezes; the label is a rendering of it. Parse the label only when
    # side is absent or unrecognised.
    explicit = str(play.get('side') or '').strip().lower()
    if explicit in ('home', 'away', 'over', 'under'):
        return explicit

    label = (play.get('label') or '').lower()
    if 'over' in label: return 'over'
    if 'under' in label: return 'under'
    # Team-based: match against home/away name
    home = (ctx.get('home_team') or '').lower()
    away = (ctx.get('away_team') or '').lower()
    if home and home in label: return 'home'
    if away and away in label: return 'away'
    # Fallback: infer from primary_play sub text or projected_spread sign
    ps = ctx.get('projected_spread')
    if ps is not None:
        return 'home' if float(ps) > 0 else 'away'
    return None


def _pick_line(play: dict, ctx: dict) -> Optional[float]:
    """Extract the numeric line from primary_play, quoted from the picked
    side. Falls back to close_spread / close_total.

    2026-09-22: prefer primary_play.line, which the scorer already stores
    from the PICKED SIDE's perspective ('BAL -8.5' -> -8.5, 'LV +6.5' ->
    +6.5). Two bugs fixed by doing so:

      * 'rl' fell through to None. nfl_game_context emits 'rl' for every
        sharp/ensemble spread pick — 9 of 18 on the 9/22 slate — so those
        rows reached the grader with no line at all and stayed pending
        forever.
      * 'spread' returned abs(close_spread), discarding both the sign and
        the side it was quoted from. A grader cannot settle 'away +3' and
        'home -3' apart from each other given only 3.

    Falling back to close_* keeps older rows working.
    """
    if not play: return None
    ptype = (play.get('type') or '').lower()

    explicit = _f(play.get('line'))
    if explicit is not None:
        return explicit

    if ptype == 'total':
        return _f(ctx.get('close_total'))
    if ptype in ('spread', 'rl'):
        # NFL convention: positive close_spread = home favoured. Re-quote
        # from the picked side so the sign means what the label shows.
        cs = _f(ctx.get('close_spread'))
        if cs is None: return None
        side = str(play.get('side') or '').lower()
        if side == 'home': return -cs
        if side == 'away': return cs
        return abs(cs)
    return None


def build_pick_row(ctx: dict) -> Optional[dict]:
    """Build one nfl_game_picks row from an nfl_game_context row.
    Returns None when the game has no primary_play (below LIGHT threshold)."""
    play = ctx.get('primary_play') or {}
    if not play:
        return None

    ptype = (play.get('type') or 'skip').lower()
    tier = play.get('tier')
    conv = play.get('signal_floor') or 0
    side = _side_from_play(play, ctx)
    if not side:
        return None
    line = _pick_line(play, ctx)

    ps = _f(ctx.get('projected_spread'))
    cs = _f(ctx.get('close_spread'))
    pt = _f(ctx.get('projected_total'))
    ct = _f(ctx.get('close_total'))
    spread_edge = round(ps - cs, 2) if (ps is not None and cs is not None) else None
    total_edge = round(pt - ct, 2) if (pt is not None and ct is not None) else None

    return {
        'game_id': ctx['game_id'],
        'game_date': ctx['game_date'],
        'season': ctx.get('season'),
        'week': ctx.get('week'),
        'season_type': ctx.get('season_type') or 'REG',
        'home_team': ctx['home_team'],
        'away_team': ctx['away_team'],
        'pick_type': ptype,
        'pick_side': side,
        'pick_label': play.get('label'),
        'pick_line': line,
        'odds_american': (
            ctx.get('close_home_ml') if side == 'home' and ptype == 'ml'
            else ctx.get('close_away_ml') if side == 'away' and ptype == 'ml'
            else None
        ),
        'tier': tier,
        'conviction': conv,
        'cohort_tags': ctx.get('cohort_tags') or [],
        'projected_spread': ps,
        'projected_total': pt,
        'close_spread': cs,
        'close_total': ct,
        'spread_edge': spread_edge,
        'total_edge': total_edge,
        'signal_confluence': ctx.get('signal_confluence_net'),
        'signals': {
            'primary_play_sub': play.get('sub'),
            'signal_floor': play.get('signal_floor'),
            'sweat_score': ctx.get('sweat_score'),
            'sweat_tier': ctx.get('sweat_tier'),
            'breakdown': ctx.get('signal_confluence_breakdown'),
        },
        'is_lock_of_week': False,  # set separately in select_lock_of_week
    }


def build_skip_rows(ctx: dict) -> list:
    """Emit skip-alert rows for chalk-trap cohorts. Helps app surface a
    consistent 'Skip Alerts' section."""
    rows = []
    for reason, fn in SKIP_COHORTS.items():
        try:
            if fn(ctx):
                rows.append({
                    'game_id': ctx['game_id'],
                    'game_date': ctx['game_date'],
                    'season': ctx.get('season'),
                    'week': ctx.get('week'),
                    'season_type': ctx.get('season_type') or 'REG',
                    'home_team': ctx['home_team'],
                    'away_team': ctx['away_team'],
                    'pick_type': 'skip',
                    'pick_side': 'skip',
                    'pick_label': f'SKIP: {reason}',
                    'tier': None,
                    'conviction': None,
                    'cohort_tags': ctx.get('cohort_tags') or [],
                    'close_spread': _f(ctx.get('close_spread')),
                    'close_total': _f(ctx.get('close_total')),
                    'signals': {'reason': reason},
                    'is_lock_of_week': False,
                })
        except Exception:
            continue
    return rows


def select_lock_of_week(picks: list) -> Optional[str]:
    """Pick the single row that becomes lock_of_week. Returns the game_id."""
    candidates = [p for p in picks
                  if p['pick_type'] not in ('skip',)
                  and p.get('tier') in ('PRIME', 'STRONG', 'LIGHT')]
    if not candidates:
        return None

    def rank_key(p):
        tier_rank = {'PRIME': 0, 'STRONG': 1, 'LIGHT': 2}.get(p['tier'], 9)
        # Cohort priority — lower index = more preferred
        cohort_rank = 99
        for i, c in enumerate(COHORT_PRIORITY):
            if c in (p.get('cohort_tags') or []):
                cohort_rank = i
                break
        conv = -(p.get('conviction') or 0)  # higher conviction first
        return (tier_rank, cohort_rank, conv)

    candidates.sort(key=rank_key)
    return candidates[0]['game_id']


class PickWriteError(RuntimeError):
    """Raised when the pick ledger write fails.

    2026-09-22: this used to print a warning and return 0, so the script
    exited 0 and the workflow's `|| echo "play_of_day failed"` never
    fired. nfl_game_picks went from 9/09 to 9/22 with a single
    regular-season row and nothing anywhere said so — Week 1 and Week 2
    have no graded pick ledger as a result. A write that does not happen
    must fail loudly; the whole point of the table is the receipt.
    """


def upsert_picks(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows:
            lock = ' 🔒 LOCK' if r.get('is_lock_of_week') else ''
            print(f"  [DRY] {r['game_id'][:12]}...  "
                  f"{r['pick_type']:6}:{r['pick_side']:5}  "
                  f"tier={r.get('tier') or '-':<6}  "
                  f"conv={r.get('conviction') or '-':<3}  "
                  f"{r.get('pick_label') or ''}{lock}")
        return len(rows)
    # 2026-09-22 ROOT CAUSE of the 9/09-9/22 ledger gap. PostgREST
    # requires every object in a batch upsert to carry an IDENTICAL key
    # set, else the whole batch 400s with PGRST102 'All object keys must
    # match'. build_pick_row and build_skip_rows emit different shapes,
    # and is_lock_of_week is added to only one row — so every mixed
    # batch was rejected outright. Combined with upsert_picks swallowing
    # the error and returning 0, the pipeline reported success while
    # writing nothing for two weeks.
    #
    # Union the keys and fill the gaps with None so every row matches.
    # (Same fix as feedback_postgrest_batch_normalize_keys.)
    all_keys = set()
    for row in rows:
        all_keys |= set(row.keys())
    rows = [{k: row.get(k) for k in all_keys} for row in rows]

    try:
        r = requests.post(
            f'{SB}/rest/v1/nfl_game_picks?on_conflict=game_id,pick_type,pick_side',
            headers=H_WRITE, json=rows, timeout=30,
        )
    except requests.exceptions.RequestException as e:
        raise PickWriteError(f'upsert raised {type(e).__name__}: {e}') from e
    if r.status_code not in (200, 201, 204):
        raise PickWriteError(f'upsert failed {r.status_code}: {r.text[:300]}')
    return len(rows)


def run(dry_run: bool = False, since: Optional[str] = None) -> None:
    print(f'=== NFL play-of-day · {_et_now().date()}'
          + (f' · BACKFILL since {since}' if since else '') + ' ===')
    contexts = load_upcoming(since=since)
    print(f'  upcoming game_context rows: {len(contexts)}')
    if not contexts:
        print('  no upcoming games — offseason or lines not yet loaded')
        return

    all_rows = []
    picks_only = []
    for ctx in contexts:
        pick = build_pick_row(ctx)
        if pick:
            all_rows.append(pick)
            picks_only.append(pick)
        all_rows.extend(build_skip_rows(ctx))

    # Flag lock_of_week — per (season, week), not per run.
    # 2026-09-22: was one lock for the whole window. Harmless for the
    # daily cron, which only ever sees one week, but a --since backfill
    # spans several and would stamp a single game while leaving the
    # other weeks with no lock at all.
    lock_gids = set()
    by_week = {}
    for p in picks_only:
        by_week.setdefault((p.get('season'), p.get('week')), []).append(p)
    for _wk, wk_picks in by_week.items():
        gid = select_lock_of_week(wk_picks)
        if gid:
            lock_gids.add(gid)
    for p in all_rows:
        if p['game_id'] in lock_gids and p['pick_type'] != 'skip':
            p['is_lock_of_week'] = True

    print(f'  picks generated: {len(picks_only)} '
          f'(+ {len(all_rows) - len(picks_only)} skip-alerts)  '
          f'lock_of_week: '
          + (', '.join(sorted(g[:12] + "..." for g in lock_gids)) if lock_gids else '—'))

    written = upsert_picks(all_rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to nfl_game_picks')

    # Tier tally
    by_tier = {'PRIME': 0, 'STRONG': 0, 'LIGHT': 0, 'LEAN': 0, 'skip': 0}
    for p in all_rows:
        t = p.get('tier') or 'skip'
        by_tier[t] = by_tier.get(t, 0) + 1
    print(f'  by tier: {by_tier}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--since', metavar='YYYY-MM-DD',
                    help='Backfill: start the context window at this date '
                         'instead of today, to write pick rows for games '
                         'already played.')
    args = ap.parse_args()
    run(dry_run=args.dry_run, since=args.since)


if __name__ == '__main__':
    main()
