"""NCAAB live-lines pull from Odds API — Phase 1.

Pulls spread, total, and moneyline for upcoming CBB games. Upserts to
ncaab_game_results on game_id. Runs nightly during CBB season
(Nov-Mar) on the NCAAB workflow cron.

Sign convention (matches existing ncaab_game_context.py):
  close_spread: home perspective — NEGATIVE = home favored.
  projected_spread: positive = home wins by X (KenPom-driven).
  This is opposite to NCAAF (which flipped to nflverse convention).
  Kept as-is because ncaab_game_context.py + sweat scorer already
  encode this convention (per feedback_backside_dictates_app_renders
  the app assumes server writes match the schema comments).

USAGE:
    python ncaab_odds_pull.py              # today + next 2 days
    python ncaab_odds_pull.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

ODDS_API_BASE = 'https://api.the-odds-api.com/v4/sports'
ODDS_SPORT = 'basketball_ncaab'
# 2026-10-21: season DERIVED, not pinned. This was hardcoded to last
# season and the 2026-27 opener is 2026-11-03, so every NCAAB card
# would have launched on 2025-26 final ratings. See ncaab_season.py.
from ncaab_season import season_label  # noqa: E402
SEASON = season_label()


def _et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def load_alias_map() -> dict:
    """Map Odds API name → canonical_name via ncaab_team_aliases."""
    r = requests.get(
        f'{SB}/rest/v1/ncaab_team_aliases?select=canonical_name,odds_api_name,alt_names',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ alias fetch failed: {r.status_code}')
        return {}
    aliases = {}
    for row in r.json():
        canon = row.get('canonical_name')
        if not canon: continue
        if row.get('odds_api_name'):
            aliases[row['odds_api_name']] = canon
        for alt in (row.get('alt_names') or []):
            if alt: aliases[alt] = canon
        aliases[canon] = canon
    return aliases


def fetch_odds_api() -> tuple[list, int]:
    if not ODDS_KEY: return [], 401
    url = (f'{ODDS_API_BASE}/{ODDS_SPORT}/odds/'
           f'?apiKey={ODDS_KEY}&regions=us,us2&markets=spreads,totals,h2h'
           f'&oddsFormat=american'
           f'&bookmakers=hardrockbet,draftkings,fanduel,espnbet,betmgm,caesars')
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ Odds API NCAAB: {r.status_code} {r.text[:120]}')
        return [], r.status_code
    return r.json(), 200


def _consensus_spread(event: dict, home_raw: str) -> Optional[float]:
    """Average home spread across books (Odds API convention: home spread as-is)."""
    vals = []
    for book in event.get('bookmakers', []):
        for m in book.get('markets', []):
            if m['key'] != 'spreads': continue
            for o in m.get('outcomes', []):
                if o.get('name') == home_raw:
                    pt = _f(o.get('point'))
                    if pt is not None: vals.append(pt)
    return round(sum(vals) / len(vals), 2) if vals else None


def _consensus_total(event: dict) -> Optional[float]:
    vals = []
    for book in event.get('bookmakers', []):
        for m in book.get('markets', []):
            if m['key'] != 'totals': continue
            for o in m.get('outcomes', []):
                if o.get('name') == 'Over':
                    pt = _f(o.get('point'))
                    if pt is not None: vals.append(pt)
    return round(sum(vals) / len(vals), 1) if vals else None


def _consensus_ml(event: dict, team_raw: str) -> Optional[int]:
    vals = []
    for book in event.get('bookmakers', []):
        for m in book.get('markets', []):
            if m['key'] != 'h2h': continue
            for o in m.get('outcomes', []):
                if o.get('name') == team_raw and o.get('price') is not None:
                    vals.append(int(o['price']))
    return int(sum(vals) / len(vals)) if vals else None


def event_to_row(event: dict, aliases: dict) -> Optional[dict]:
    home_raw = event.get('home_team')
    away_raw = event.get('away_team')
    home = aliases.get(home_raw)
    away = aliases.get(away_raw)
    if not home or not away:
        return None

    commence = event.get('commence_time', '')
    try:
        dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
        game_date = dt.date().isoformat()
    except Exception:
        dt = _et_now(); game_date = dt.date().isoformat()

    game_id = f'ncaab_{dt.strftime("%Y%m%d")}_{away}_{home}'.replace(' ', '_')

    home_spread = _consensus_spread(event, home_raw)  # Odds API home spread
    row = {
        'game_id': game_id,
        'game_date': game_date,
        'season': SEASON,
        'home_team': home,
        'away_team': away,
        # NCAAB convention: close_spread NEGATIVE = home favored → keep Odds API sign
        'close_spread': home_spread,
        'close_total': _consensus_total(event),
        'home_ml_close': _consensus_ml(event, home_raw),
        'away_ml_close': _consensus_ml(event, away_raw),
    }
    # Mirror close → open on first pull (open_* preserved once set by prior row)
    row['open_spread'] = row['close_spread']
    row['open_total']  = row['close_total']
    row['home_ml_open'] = row['home_ml_close']
    row['away_ml_open'] = row['away_ml_close']
    return row


#: The context table the ENGINE and the SHARP read. This puller has always
#: written ncaab_game_results only, which is why every NCAAB pick was
#: priceless -- see the block comment on patch_context.
CTX_TABLE = 'ncaab_game_context'

#: Price columns, written under BOTH alias families on purpose.
#: generate_sharp_card reads close_home_ml/close_away_ml for NCAAB (its own
#: comment at line ~481 documents the per-sport alias mess: NFL/NCAAF use
#: close_home_ml, MLB/NBA/NHL use home_ml_close) while this puller's row
#: builder writes home_ml_close. They are two names for one number, and
#: filling only one is the entire defect, so both are set from the same
#: value. Nothing else on the row is touched.


def event_to_ctx_patch(event: dict, aliases: dict):
    """(odds-api event id, price-only patch) for ncaab_game_context.

    Keyed on the ODDS API EVENT ID, which IS ncaab_game_context.game_id --
    verified 7 of 7 against live rows, so no name or date matching is
    needed and none is attempted.
    """
    home_raw, away_raw = event.get('home_team'), event.get('away_team')
    if not aliases.get(home_raw) or not aliases.get(away_raw):
        return None
    eid = event.get('id')
    if not eid:
        return None
    home_spread = _consensus_spread(event, home_raw)
    total = _consensus_total(event)
    hml = _consensus_ml(event, home_raw)
    aml = _consensus_ml(event, away_raw)
    if home_spread is None and total is None and hml is None and aml is None:
        return None          # nothing posted yet; do not blank the row
    patch = {
        'close_spread': home_spread,
        'close_total': total,
        'home_ml_close': hml,
        'away_ml_close': aml,
        'close_home_ml': hml,
        'close_away_ml': aml,
    }
    return eid, patch


def patch_context(pairs: list, dry_run: bool = False) -> dict:
    """Carry the posted prices onto the rows the engine actually reads.

    == 2026-10-30 . NCAAB WAS UNBETTABLE BECAUSE THE PRICES NEVER ARRIVED =
    ANDY: "The Steam Room needs to find a way to play nba, ncaab when
    online and all other sports when live. THIS IS A MUST."

    The Sharp took 0 of 3 bettable NCAAB picks, and its own dry run said
    why: "NCAAB no-market-price drops: 3 (ML pick with no book moneyline --
    unbettable)". Every ncaab_game_context row had close_spread,
    close_total and both moneylines NULL while the engine still formed an
    ML opinion on it -- an opinion with no market anchor at all.

    The data was never missing. This puller fetches full spreads, totals
    and both moneylines for every game and writes them to
    ncaab_game_RESULTS under a slug id (ncaab_20261101_Villanova_Notre_Dame)
    while the context rows are keyed by the Odds API event id
    (cf863e45a3252d...) and dated differently again. Two tables, two key
    schemes, no join -- the same family as R80 (line_snapshot vs
    nfl_game_results) and the NCAAF team-name ids.

    PATCH, never upsert. The context builder owns row creation; this only
    fills price columns on rows that already exist, so a schedule the
    builder has not seen yet cannot be conjured here. Rows with no context
    match are counted and named rather than silently dropped.

    open_* is deliberately NOT touched. Those columns are already populated
    and an opening price is not a closing one; overwriting them would
    destroy the only line-movement record NCAAB has.

    Confirms by READBACK. A PostgREST PATCH that matches no row returns
    200 with an empty body, which is indistinguishable from success
    (feedback_204_is_not_a_write).
    """
    out = {'patched': 0, 'no_ctx_row': 0, 'unconfirmed': 0}
    if not pairs:
        return out
    for eid, patch in pairs:
        if dry_run:
            print(f'  [DRY] ctx {eid} sp={patch["close_spread"]} '
                  f'tot={patch["close_total"]} '
                  f'ml={patch["close_home_ml"]}/{patch["close_away_ml"]}')
            out['patched'] += 1
            continue
        r = requests.patch(
            f'{SB}/rest/v1/{CTX_TABLE}', headers=H_WRITE, timeout=30,
            params={'game_id': f'eq.{eid}'}, json=patch)
        # == 2026-10-30 . 204 HERE MEANS WRITTEN, NOT MISSING ============
        # H_WRITE carries Prefer: return=minimal, so a SUCCESSFUL patch
        # returns 204 with an empty body. My first version read that empty
        # body as "no such row" and reported "0 priced, 7 with no context
        # row" on the run that in fact priced all seven. A verifier that
        # cries failure on success hides a real miss just as effectively as
        # one that cries success on failure, so the confirmation is now an
        # explicit GET that does not depend on Prefer semantics at all.
        if r.status_code not in (200, 201, 204):
            out['unconfirmed'] += 1
            print(f'  ! ctx {eid} HTTP {r.status_code} {r.text[:120]}')
            continue
        chk = requests.get(
            f'{SB}/rest/v1/{CTX_TABLE}', headers=H_READ, timeout=30,
            params={'game_id': f'eq.{eid}',
                    'select': 'game_id,close_home_ml,close_spread'})
        rows = chk.json() if chk.status_code == 200 else []
        if not rows:
            out['no_ctx_row'] += 1
            print(f'  · no ncaab_game_context row for event {eid} -- the '
                  f'context builder has not created it yet, so there is '
                  f'nothing to price')
            continue
        if rows[0].get('close_home_ml') != patch['close_home_ml']:
            out['unconfirmed'] += 1
            print(f'  ! ctx {eid} NOT CONFIRMED: close_home_ml reads '
                  f'{rows[0].get("close_home_ml")!r}, expected '
                  f'{patch["close_home_ml"]!r} -- a trigger may be '
                  f'refusing the write')
            continue
        out['patched'] += 1
    return out

def _normalize_batch_keys(rows: list) -> list:
    """Union keys + backfill None across rows (PGRST102 batch upsert requirement).
    Per feedback_postgrest_batch_normalize_keys memory."""
    if not rows: return rows
    keys = set()
    for r in rows: keys.update(r.keys())
    for r in rows:
        for k in keys: r.setdefault(k, None)
    return rows


def upsert_games(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    rows = _normalize_batch_keys(rows)
    if dry_run:
        for r in rows[:15]:
            print(f"  [DRY] {r['game_id']} sp={r.get('close_spread')} "
                  f"tot={r.get('close_total')} ml={r.get('home_ml_close')}/{r.get('away_ml_close')}")
        if len(rows) > 15:
            print(f'  [DRY] ... {len(rows)-15} more')
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/ncaab_game_results?on_conflict=game_id',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def is_pregame(event: dict, grace_min: int = 0) -> bool:
    """True only if this event has NOT started yet.

    2026-09-21, added before raising poll cadence. The Odds API /odds
    endpoint returns IN-PROGRESS games with live in-game prices, and none
    of these pullers filtered on commence_time. At one pull a day that
    rarely mattered. Polling every 30 minutes it matters a great deal:
    a live line would be written straight into close_spread / close_total,
    which is the number grading compares against — so we would silently
    corrupt the closing line and therefore every spread and total result
    derived from it. Live prices would also manufacture fake "steam" in
    line_history, since an in-game total has no relationship to the
    pre-game one.

    This is the same hazard mlb_line_poller guards by locking close_total
    within 10 min of first pitch (project_pm_cron_live_game_prop_overwrite).
    Here we simply drop started games entirely: the last pre-game poll is
    the close, which is what the column is supposed to mean.
    """
    ct = event.get('commence_time')
    if not ct:
        return True          # no timestamp to judge by — keep, don't guess
    try:
        dt = datetime.fromisoformat(str(ct).replace('Z', '+00:00'))
    except ValueError:
        return True
    return dt > datetime.now(timezone.utc) + timedelta(minutes=grace_min)


def _emit_line_history(pairs: list, sport: str) -> int:
    """Write line_history rows from the slate this puller already fetched.

    2026-09-21. line_history feeds detect_line_movement -> line_movement_flags
    -> classify_line_moves and the Steam Room Split view. NFL and NCAAF rows
    STOPPED on 2026-09-09 — twelve days of the season with no line-movement
    detection at all, which is why football carried only 51 NFL flags while
    MLB had 1,378.

    Cause: write_line_history.py reads odds_cache, which only fills when a
    user opens the Games tab. The newest odds_games_* row for ANY sport is
    2026-09-09. MLB escaped because line_poller calls
    write_line_history_from_event directly — a writer added 09-11 precisely
    to bypass odds_cache, but wired for MLB only.

    Never fatal: losing line_history must not cost us the odds pull itself.
    """
    if not pairs:
        return 0
    # Proximity gate — see odds_pull_core._write_line_history for why.
    # Set by the poller workflow; unset in the daily pipeline so that run
    # still captures the full forward slate once a day.
    _max_out = os.environ.get('LINE_HISTORY_MAX_HOURS_OUT')
    if _max_out:
        try:
            _cut = datetime.now(timezone.utc) + timedelta(hours=float(_max_out))
            _kept = []
            for _ev, _gid, _gd in pairs:
                _ct = _ev.get('commence_time')
                if not _ct:
                    _kept.append((_ev, _gid, _gd)); continue
                try:
                    if datetime.fromisoformat(str(_ct).replace('Z', '+00:00')) <= _cut:
                        _kept.append((_ev, _gid, _gd))
                except ValueError:
                    _kept.append((_ev, _gid, _gd))
            _skip = len(pairs) - len(_kept)
            pairs = _kept
            if _skip:
                print(f'    line_history: skipped {_skip} game(s) beyond {_max_out}h out')
            if not pairs:
                return 0
        except ValueError:
            pass
    try:
        from book_lines_writer import write_line_history_from_event
    except Exception as e:
        print(f'    ⚠ line_history writer unavailable ({e})')
        return 0
    total = failed = 0
    for ev, gid, gd in pairs:
        try:
            total += write_line_history_from_event(ev, sport, gid, gd) or 0
        except Exception:
            failed += 1
    msg = f'    line_history: {total} rows from {len(pairs)} events'
    if failed:
        msg += f' ({failed} failed)'
    print(msg)
    return total


def run(dry_run: bool = False) -> None:
    print(f'=== NCAAB odds pull · {_et_now().date()} ===')
    if not ODDS_KEY:
        print('  ✗ ODDS_API_KEY missing — abort'); return

    aliases = load_alias_map()
    if not aliases:
        print('  ✗ ncaab_team_aliases empty — run ncaab_seed_aliases.py first'); return
    print(f'  alias map: {len(aliases)} name variants')

    events, status = fetch_odds_api()
    if status != 200: return
    print(f'  Odds API events: {len(events)}')
    if not events:
        print('  (offseason or no lines posted)'); return

    rows, skipped, lh_pairs, ctx_pairs = [], [], [], []
    for event in events:
        if not is_pregame(event):
            continue
        row = event_to_row(event, aliases)
        if row is None:
            skipped.append((event.get('home_team'), event.get('away_team')))
            continue
        rows.append(row)
        lh_pairs.append((event, row['game_id'], row['game_date']))
        # Same pregame filter and same alias gate -- the context patch must
        # not see an event the results row already refused.
        cp = event_to_ctx_patch(event, aliases)
        if cp is not None:
            ctx_pairs.append(cp)
    if skipped:
        print(f'  ⚠ skipped {len(skipped)} events w/ unmapped teams:')
        for h, a in skipped[:8]:
            print(f'      {a} @ {h}')

    if not dry_run:
        _emit_line_history(lh_pairs, 'NCAAB')
    written = upsert_games(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to ncaab_game_results')

    # The half that was missing: carry the same prices onto the rows the
    # engine and the Sharp actually read. Without this NCAAB is priceless
    # and every pick is dropped as unbettable.
    ctx = patch_context(ctx_pairs, dry_run=dry_run)
    print(f"{prefix}ncaab_game_context: {ctx['patched']} priced, "
          f"{ctx['no_ctx_row']} with no context row, "
          f"{ctx['unconfirmed']} unconfirmed")
    if ctx['unconfirmed']:
        print('  ! an unconfirmed patch means the write did not stick - '
              'check for a trigger before trusting the slate')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
