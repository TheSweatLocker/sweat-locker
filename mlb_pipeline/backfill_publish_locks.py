"""Backfill publish_lock from today's live surface caches.

Andy 9/17: shipping the publish-lock system mid-day means TODAY's
already-published picks have no lock rows yet. This script walks the
current cache surfaces (jerry_cache sweat_card + sharp_card entries,
plus prop_jerry_reads for individual prop cards) and locks each pick
at the tier it CURRENTLY shows on the surface.

Not perfect — the surface cache was written earlier today at whatever
tier the row had then. If the LIVE tier has since flipped and the
surface hasn't been regenerated, the surface + DB disagree; we lock
what the SURFACE shows (the honest "what user saw" reading).

CLI:
  python backfill_publish_locks.py                     # write live
  python backfill_publish_locks.py --dry-run           # print only
  python backfill_publish_locks.py --date YYYY-MM-DD
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

from prop_publish_lock import lock_publish  # noqa: E402


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _lookup_prop_id(sport: str, game_date: str, player_name: str,
                    prop_type: str, direction: str) -> int | None:
    """Resolve prop_line row id from (sport, game_date, player, type, dir)."""
    tbl = 'mlb_pipeline_props' if sport == 'MLB' else 'nfl_pipeline_props'
    r = requests.get(
        f'{SB}/rest/v1/{tbl}',
        headers=H_READ,
        params={
            'game_date': f'eq.{game_date}',
            'player_name': f'eq.{player_name}',
            'prop_type': f'eq.{prop_type}',
            'direction': f'eq.{direction}',
            'select': 'id',
            'limit': '1',
        },
        timeout=10,
    )
    if r.status_code != 200: return None
    rows = r.json()
    return rows[0]['id'] if rows else None


def backfill_sweat_card(game_date: str, dry_run: bool = False) -> int:
    """Lock every top_8 pick from the Sweat Card cache for `game_date`."""
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_READ,
        params={'cache_key': f'eq.sweat_card_{game_date}',
                'select': 'sport,data'},
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    n = 0
    for row in rows:
        sport = row.get('sport') or 'ALL'
        d = row.get('data') or {}
        picks = d.get('top_8') or d.get('picks') or []
        for p in picks:
            if not isinstance(p, dict): continue
            tier = (p.get('tier') or '').upper()
            conv = p.get('conviction')
            source = p.get('source_key') or p.get('source_id') or p.get('id')
            # 2026-09-17: normalize market. sweat_card items sometimes
            # carry `market='outs_under'` (the prop family) instead of
            # the canonical 'prop' — earlier backfill wrote 2 rows as
            # market='outs_under' which the grader (filters market='prop')
            # never matched. Explicit 4-value canonical set.
            pt = (p.get('type') or '').lower()
            m_raw = (p.get('market') or '').lower()
            if pt == 'prop' or p.get('prop_type') or m_raw not in ('ml','rl','total'):
                market = 'prop' if (pt == 'prop' or p.get('prop_type')) else m_raw
            else:
                market = m_raw
            if market not in ('prop','ml','rl','total'):
                continue
            pick_sport = (p.get('sport') or sport or '').upper()
            if not source: continue
            if lock_publish(pick_sport, market, source, tier, conv,
                            'sweat_card', dry_run=dry_run):
                n += 1
    return n


def backfill_sharp_card(game_date: str, dry_run: bool = False) -> int:
    """Lock every item from the Sharp Card (Steam Room) cache."""
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_READ,
        params={'cache_key': f'eq.sharp_card_{game_date}',
                'select': 'sport,data'},
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    n = 0
    for row in rows:
        sport = row.get('sport') or 'ALL'
        d = row.get('data') or {}
        items = d.get('items') or d.get('picks') or []
        for p in items:
            if not isinstance(p, dict): continue
            tier = (p.get('tier') or '').upper()
            conv = p.get('conviction')
            market = p.get('prop_type') and 'prop' or p.get('market') or p.get('type')
            pick_sport = (p.get('sport') or sport or '').upper()
            if market == 'prop':
                # 2026-09-17: prefer p['id'] (present when the composer
                # wrote the item post-composer-lock-wire commit 29761759).
                # Fall back to lookup only for legacy items missing id —
                # that path relied on player_name / prop_type / direction
                # being on the item, which sharp_card items only carry
                # in the raw formatted `pick` string, so lookup returned
                # None for every legacy row.
                sid = p.get('id')
                if not sid:
                    sid = _lookup_prop_id(
                        pick_sport, game_date,
                        p.get('player_name'), p.get('prop_type'), p.get('direction'),
                    )
                if not sid: continue
                if lock_publish(pick_sport, 'prop', sid, tier, conv,
                                'sharp_card', dry_run=dry_run):
                    n += 1
            else:
                # Side pick — source_id = game_id
                gid = p.get('game_id')
                if not gid or not market: continue
                if lock_publish(pick_sport, market, gid, tier, conv,
                                'sharp_card', dry_run=dry_run):
                    n += 1
    return n


def _page(path: str, params: dict) -> list[dict]:
    """Paginated read. The prior prop path used a Range header capped at
    9999 and then issued TWO more requests per row; on a full MLB slate
    that was ~3,300 round trips for one step. PostgREST also truncates
    silently at 1000 without explicit paging (project_postgrest_truncation
    _audit_912), so page properly rather than trusting one large read."""
    out: list[dict] = []
    off = 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H_READ,
                         params=q, timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ⚠ read {path} -> {r.status_code}: {(r.text or "")[:160]}')
            return out
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


_PROPS_TABLE = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props'}


def backfill_prop_jerry_reads(game_date: str, dry_run: bool = False,
                              reset_ungraded: bool = False) -> int:
    """Lock every prop_jerry_reads row (every prop card that a user
    could have opened on the Prop Jerry tab) at its SOURCE prop tier.

    2026-09-25: this is now the canonical prop lock point, called from the
    pipeline AFTER the discipline passes. It used to run inline inside
    generate_prop_jerry_synthesis (MLB step 877), which locked before
    apply_refit_verdict_override (927) could demote anything — and the
    20260917f trigger then reverted every demotion while returning 200.
    See the note at the old call site.

    Joins in memory off two paginated reads instead of the previous
    per-row (lookup id, then fetch tier) pair of requests.

    ══ WHY THIS LOCKS GAME-DAY ONLY, AND WHY THAT LOOKS LIKE A BUG ══
    The re-lock is scoped to game_date == game_date. On a Friday run that
    means ~145 MLB props and ZERO NFL props, because NFL props are for
    Sunday. That is deliberate, not a missed filter.

    A prop is only FINAL on its own game day. Sunday's NFL board is
    genuinely still moving on Friday — lines shift, props regenerate, and
    Saturday's discipline passes still have to be able to demote. Locking
    a Sunday prop on Friday recreates the exact bug this move fixes, just
    two days earlier instead of 50 workflow steps earlier. So each slate
    gets locked by its own game-day run, after that day's discipline.

    The RESET scope is deliberately wider — see reset_ungraded below.
    """
    n = 0
    for sport, tbl in _PROPS_TABLE.items():
        # ── reset: every unplayed prop, not just today's ──
        # Locks written by the old inline call site are premature at EVERY
        # date, so clearing only today's would leave Sunday's NFL board
        # frozen at its pre-discipline tier. Graded rows are excluded
        # inside _clear_ungraded_locks, so this cannot move a record.
        if reset_ungraded:
            future = _page(tbl, {
                'game_date': f'gte.{game_date}',
                'select': 'id,result',
            })
            n_cleared = _clear_ungraded_locks(sport, future, dry_run=dry_run)
            print(f'  {sport}: cleared {n_cleared} premature ungraded prop '
                  f'locks (game_date >= {game_date})')
            # Deliberately NO relock in this mode. Relocking right after a
            # reset would re-freeze the same pre-discipline tier we just
            # released, making the reset a no-op. The correct relock happens
            # on the next pipeline run, at the post-discipline lock step.
            continue

        # ── re-lock: game day only ──
        reads = _page('prop_jerry_reads', {
            'game_date': f'eq.{game_date}',
            'sport': f'eq.{sport}',
            'select': 'player_name,prop_type,direction',
        })
        if not reads:
            print(f'  {sport}: no prop_jerry_reads for {game_date} — '
                  f'nothing to lock (slate is on another date)')
            continue
        want = {(r.get('player_name'), r.get('prop_type'), r.get('direction'))
                for r in reads}
        props = _page(tbl, {
            'game_date': f'eq.{game_date}',
            'select': 'id,player_name,prop_type,direction,tier,conviction,result',
        })
        # Key on the same tuple the old _lookup_prop_id matched on.
        matched = [p for p in props
                   if (p.get('player_name'), p.get('prop_type'),
                       p.get('direction')) in want]
        locked = 0
        for p in matched:
            if lock_publish(sport, 'prop', p['id'],
                            (p.get('tier') or '').upper(), p.get('conviction'),
                            'prop_jerry', dry_run=dry_run):
                locked += 1
        print(f'  {sport}: {locked} locked of {len(matched)} matched '
              f'({len(reads)} jerry reads)')
        n += locked
    return n


def _clear_ungraded_locks(sport: str, props: list[dict],
                          dry_run: bool = False) -> int:
    """Delete prop locks for props that have NOT been graded yet.

    Needed only when migrating the lock point. lock_publish is
    first-publisher-wins (ON CONFLICT DO NOTHING), so a lock already
    written at the wrong moment would keep winning forever and the move
    would only take effect on tomorrow's slate.

    GRADING SAFETY — this cannot alter any record. A lock only matters to
    compute_surface_records at grade time; a prop with result IS NULL has
    never been counted in anything. Rows WITH a result are skipped, full
    stop, so no settled number can move. Re-locking happens immediately
    after, at the post-discipline tier.
    """
    ungraded = {str(p['id']) for p in props if p.get('result') is None}
    if not ungraded:
        return 0
    # Intersect with locks that actually EXIST. Without this the dry run
    # reported every ungraded prop as "cleared" (1,668 for MLB when only
    # 145 held a lock), and the live path issued DELETEs for rows that
    # were never locked. The count has to mean something to be worth
    # printing before a destructive step.
    locked_ids = {str(r['source_id']) for r in _page('publish_lock', {
        'sport': f'eq.{sport}', 'market': 'eq.prop', 'select': 'source_id',
    })}
    targets = sorted(ungraded & locked_ids)
    if not targets:
        return 0
    cleared = 0
    for i in range(0, len(targets), 100):
        chunk = targets[i:i + 100]
        ids = ','.join(f'"{c}"' for c in chunk)
        if dry_run:
            cleared += len(chunk)
            continue
        r = requests.delete(
            f'{SB}/rest/v1/publish_lock',
            headers={**H_READ, 'Prefer': 'return=representation'},
            params={'sport': f'eq.{sport}', 'market': 'eq.prop',
                    'source_id': f'in.({ids})'},
            timeout=60,
        )
        if r.status_code in (200, 204):
            body = r.json() if r.status_code == 200 else []
            cleared += len(body) if isinstance(body, list) else len(chunk)
        else:
            print(f'  ⚠ lock clear -> {r.status_code}: {(r.text or "")[:160]}')
    return cleared


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--props-only', action='store_true',
                   help='lock props only — the in-pipeline lock step. The '
                        'card surfaces are composed LATER in the run, so '
                        'locking them from here would snapshot a stale cache.')
    p.add_argument('--reset-ungraded', action='store_true',
                   help='drop existing prop locks on UNGRADED props before '
                        'relocking. One-time migration aid for moving the '
                        'lock point; graded props are never touched.')
    args = p.parse_args()
    gd = args.date or _today_et()

    mode = ' props-only' if args.props_only else ''
    print(f'=== backfill_publish_locks · {gd}{mode}'
          f'{" [DRY]" if args.dry_run else ""} ===')
    print()

    s = p_ = 0
    if not args.props_only:
        s = backfill_sweat_card(gd, dry_run=args.dry_run)
        print(f'  sweat_card:        {s} locks')
        p_ = backfill_sharp_card(gd, dry_run=args.dry_run)
        print(f'  sharp_card:        {p_} locks')
    j = backfill_prop_jerry_reads(gd, dry_run=args.dry_run,
                                  reset_ungraded=args.reset_ungraded)
    print(f'  prop_jerry_reads:  {j} locks')
    print()
    print(f'  total: {s + p_ + j} locks written')


if __name__ == '__main__':
    main()
