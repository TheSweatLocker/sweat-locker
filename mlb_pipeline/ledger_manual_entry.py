"""Hand-enter, edit and remove Ledger plays.

WHY THIS EXISTS
---------------
Andy 2026-09-24: "I will probably hand select the plays for Sunday if possible"
and "make sure we have ability via backend to change plays in ledger".

Every one of the 16 ledger rows since 09-15 carries auto_generated=true. The
column existed, so manual entry was anticipated, but nothing ever wrote a
manual row and there was no way to add, change or pull a Ledger play without
hand-editing the DB.

The shipped app already promises this: the "THE LEDGER METHOD" card in
app/index.tsx reads "Editable builder coming soon — pick your own legs". This
is the backend half of that promise.

SURVIVES THE PIPELINE. generate_ledger.clear_todays() scopes its delete to
`auto_generated=eq.true`, so rows written here are not wiped when the pipeline
re-runs or when the rescue step fires. Verified at generate_ledger.py:1181.

GETS GRADED. snapshot_ledger.py selects every row for the date with no filter
on kind or auto_generated, so manual rows are snapshotted into
ledger_snapshots and graded by grade_ledger_snapshots.py like any other. That
is also why --kind and --odds matter: snapshots are unique on
(game_date, kind, sport_scope, combined_odds), so two manual plays that match
on all four collapse to one snapshot and only one gets a record. This tool
refuses that collision rather than letting a play vanish from the receipts.

THE CLIENT IS SHIPPED — MATCH ITS CONTRACT.
v1.0.1 is live on the App Store and renders these rows verbatim
(app/index.tsx:17770-17836). Three consequences, each of which was a live bug
in the first draft of this file:

  * Leg price is read as `leg.teased_odds ?? leg.original_odds`. A leg that
    carries `odds` renders a blank price. Keys written here are the app's.
  * `kind` falls back to `kind.toUpperCase()` when it isn't in the client's
    KIND_META map — an invented kind like "manual_teaser" would ship to paying
    users as the raw string MANUAL_TEASER in muted grey. Only the five kinds
    the shipped client knows are allowed; --kind refuses anything else.
  * `rank` is INT NOT NULL DEFAULT 0 and the app orders by rank asc, so a row
    left at the default leads the tab ahead of the generator's rank-1.

TEASE DIRECTION — the one thing this refuses to get wrong.
A tease moves the line in YOUR favour. Confirmed with Andy 2026-09-24 after he
flagged his own example was backwards:

    UNDER   total  + points   U 46.5 -> U 51.5     (more room to stay under)
    OVER    total  - points   O 46.5 -> O 41.5
    DOG     spread + points   +6.5   -> +14.5      (more cushion)
    FAV     spread + points   -8.5   -> -1.5       (less to cover)

Note the spread rows: on a signed line BOTH sides move the same way, +points.
Subtracting for favourites — which reads intuitively as "moving the line down" —
teases every favourite the wrong way, harder rather than easier. Favourites must
therefore be entered with their real negative number; an unsigned FAV is
refused as ambiguous rather than guessed at.

--tease applies that from side and market and raises on a market it cannot
reason about. A ledger full of systematically wrong-side teases would be worse
than no ledger at all.

USAGE
    # what is on the board
    python ledger_manual_entry.py --list --date 2026-09-27

    # 2-leg 6-point teaser, price read off the book
    python ledger_manual_entry.py --date 2026-09-27 --kind teaser \\
        --leg "NFL|total|UNDER|46.5|-110|Bills @ Jets|<game_id>" \\
        --leg "NFL|spread|DOG|6.5|-110|Bears @ Lions|<game_id>" \\
        --tease 6 --odds -120 --rank 1 \\
        --reasoning "Both legs cross 7 and 3." --commit

    # two -250 moneylines as straight legs (no tease) ~ +96
    python ledger_manual_entry.py --kind chalk_parlay \\
        --leg "NCAAB|ml|HOME|0|-250|Duke @ UNC" \\
        --leg "NCAAB|ml|AWAY|0|-250|Kansas @ Baylor" --commit

    python ledger_manual_entry.py --edit 791 --odds +105
    python ledger_manual_entry.py --remove 791 --commit

Nothing writes without --commit. Manual rows are stamped auto_generated=false
so their record stays separable from the generator's.
"""
import argparse
import os
import sys
import json
from datetime import datetime, timedelta, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

TABLE = 'ledger_suggestions'

# The kinds the SHIPPED v1.0.1 client has labels for (app/index.tsx KIND_META).
# Anything else renders to paying users as a raw uppercased string. Until a new
# build ships, these are the only legal values.
SHIPPED_KINDS = {
    'chalk_parlay': 'CHALK DUO/TRIO/QUAD (leg-count aware)',
    'teaser': 'TEASER',
    'teased_totals_combo': 'TEASED TOTALS COMBO',
    'teased_spreads_combo': 'TEASED SPREADS COMBO',
    'chalk_prop_parlay': 'CHALK PROP PARLAY',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def american_to_decimal(odds: float) -> float:
    return 1.0 + (odds / 100.0 if odds > 0 else 100.0 / abs(odds))


def decimal_to_american(dec: float) -> int:
    if dec <= 1.0:
        return -100000
    return int(round((dec - 1.0) * 100)) if dec >= 2.0 \
        else int(round(-100.0 / (dec - 1.0)))


def tease_line(market: str, side: str, line: float, points: float) -> float:
    """Move `line` by `points` in the direction that HELPS the bettor.

    Raises on a market/side it cannot reason about rather than guessing — a
    silent wrong-way tease is the failure mode this tool exists to prevent.
    """
    m = (market or '').lower()
    s = (side or '').upper()
    if m == 'total':
        if s == 'UNDER':
            return line + points
        if s == 'OVER':
            return line - points
        raise ValueError(f"total side must be OVER or UNDER, got {side!r}")
    if m in ('spread', 'rl', 'puckline'):
        # On a SIGNED spread the favourable move is always +points, for both
        # sides — the dog gets more cushion (+6.5 -> +14.5) and the favourite
        # has less to cover (-8.5 -> -1.5). Splitting on DOG/FAV and
        # subtracting for favourites was a bug: it teased every favourite the
        # wrong way, harder instead of easier.
        if s == 'FAV' and line > 0:
            raise ValueError(
                f"FAV line must be negative — got +{line:g}. Enter a favourite "
                f"as its real signed number (e.g. -8.5), otherwise the teased "
                f"side is ambiguous.")
        if s == 'DOG' and line < 0:
            raise ValueError(
                f"DOG line must be positive — got {line:g}. A dog takes points.")
        if s not in ('DOG', 'FAV'):
            raise ValueError(f"spread side must be DOG or FAV, got {side!r}")
        return line + points
    raise ValueError(f"cannot tease market {market!r} — a moneyline has no line "
                     f"to move. Enter MLs with no --tease.")


def parse_leg(spec: str, tease_pts: float) -> dict:
    """`sport|market|side|line|odds|matchup|game_id|teased_odds` -> leg dict.

    Keys are the ones app/index.tsx reads: pick, matchup, original_odds,
    teased_odds, teased_line, price_source. `odds` is deliberately NOT a key —
    the shipped client does not read it.
    """
    parts = [p.strip() for p in spec.split('|')]
    if len(parts) < 5:
        raise ValueError(
            f"leg needs at least sport|market|side|line|odds — got {spec!r}")
    sport, market, side, line_s, odds_s = parts[:5]
    matchup = parts[5] if len(parts) > 5 and parts[5] else None
    game_id = parts[6] if len(parts) > 6 and parts[6] else None
    teased_odds = int(float(parts[7])) if len(parts) > 7 and parts[7] else None
    try:
        line = float(line_s)
    except ValueError:
        line = None
    # A moneyline has no line, and its `side` field carries the team, not a
    # keyword — so keep it verbatim. The generator's ML picks read
    # "Pittsburgh Pirates ML"; upper-casing would ship "DUKE ML".
    is_ml = market.lower() in ('ml', 'moneyline')
    if is_ml:
        line = None
        pick = side if side.upper().endswith(' ML') else f'{side} ML'
    else:
        side = side.upper()
        pick = f'{side} {line:g}' if line is not None else side
    leg = {
        'sport': sport.upper(),
        'market': 'ml' if is_ml else market.lower(),
        'side': side,
        'line': line,
        'original_odds': int(float(odds_s)),
        'matchup': matchup,
        'game_id': game_id,
        'pick': pick,
    }
    if teased_odds is not None:
        leg['teased_odds'] = teased_odds
    if tease_pts and line is None:
        # Say so rather than quietly shipping an unteased leg inside a play
        # whose whole premise is the tease.
        raise ValueError(
            f"--tease {tease_pts:g} was given but this leg has no line to move "
            f"({'moneyline' if is_ml else f'unparseable line {line_s!r}'}). "
            f"Enter moneylines in a separate play with no --tease.")
    if tease_pts:
        teased = tease_line(market, side, line, tease_pts)
        leg['original_line'] = line
        leg['teased_line'] = teased
        leg['tease_points'] = tease_pts
        leg['pick'] = f'{side.upper()} {line:g}'
        # price_source drives the app's BOOK/EST badge, shown only on teased
        # legs. A hand-entered price was read off a book, so 'book' is honest —
        # unlike the generator's estimator-derived teased prices.
        leg['price_source'] = 'book'
    return leg


def parse_legs(specs: list, tease_pts: float):
    """Parse every --leg, reporting a bad one as a refusal not a traceback."""
    out = []
    for spec in specs:
        try:
            out.append(parse_leg(spec, tease_pts))
        except ValueError as e:
            print(f'  REFUSED: {e}')
            print(f'           leg: {spec}')
            return None
    return out


def leg_price(leg: dict):
    """Exactly what the app displays: `teased_odds ?? original_odds`.

    Mirrors JS `??`, which falls back on an explicit null. Python's
    dict.get(k, default) does NOT — it returns the stored None when the key is
    present. The generator writes teased_odds=None on untease-able ML legs, so
    a .get(k, fallback) here reports a blank price on every chalk parlay and
    makes it look like a live app bug. It isn't; the app is correct.
    """
    tp = leg.get('teased_odds')
    return tp if tp is not None else leg.get('original_odds')


def show(rows: list) -> None:
    if not rows:
        print('  (nothing on the board)')
        return
    print(f"  {'id':>6s} {'date':11s} {'kind':22s} {'odds':>7s} {'rk':>3s} "
          f"{'auto':>5s}  result")
    for r in rows:
        legs = r.get('legs') or []
        co = r.get('combined_odds')
        print(f"  {str(r.get('id')):>6s} {str(r.get('game_date'))[:10]:11s} "
              f"{str(r.get('kind'))[:21]:22s} "
              f"{(f'{co:+d}' if isinstance(co, int) else str(co)):>7s} "
              f"{str(r.get('rank')):>3s} {str(r.get('auto_generated')):>5s}  "
              f"{r.get('result') or 'ungraded'}")
        for lg in legs:
            tl = lg.get('teased_line')
            arrow = f"{lg.get('original_line')} -> {tl:g}" if tl is not None else ''
            px = leg_price(lg)
            px_s = f'{px:+d}' if isinstance(px, int) else str(px)
            print(f"         {lg.get('sport', '?'):5s} {str(lg.get('market')):7s} "
                  f"{str(lg.get('pick'))[:24]:24s} {arrow:>16s}  {px_s:>6s}   "
                  f"{lg.get('matchup') or '(no matchup)'}")


def fetch_date(gd: str) -> list:
    r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H, timeout=60,
                     params={'select': '*', 'game_date': f'eq.{gd}',
                             'order': 'rank.asc,id.asc'})
    if r.status_code != 200:
        print(f'  fetch failed {r.status_code} {(r.text or "")[:200]}')
        sys.exit(1)
    return r.json()


def check_snapshot_collision(gd, kind, scope, odds, skip_id=None) -> bool:
    """ledger_snapshots is unique on (game_date, kind, sport_scope,
    combined_odds). A second row matching all four never becomes its own
    snapshot, so it never gets graded. Refuse rather than lose the receipt."""
    for r in fetch_date(gd):
        if skip_id is not None and str(r.get('id')) == str(skip_id):
            continue
        if (r.get('kind') == kind and r.get('sport_scope') == scope
                and r.get('combined_odds') == odds):
            print(f'\n  REFUSED: id={r.get("id")} already holds '
                  f'({gd}, {kind}, {scope}, {odds:+d}).')
            print(f'  ledger_snapshots is unique on those four, so this play '
                  f'would never be graded.')
            print(f'  Change --odds, --kind, or --sport-scope to separate them.')
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--date', default=None)
    ap.add_argument('--kind', default='teaser',
                    help='one of: ' + ', '.join(sorted(SHIPPED_KINDS)))
    ap.add_argument('--leg', action='append', default=[],
                    help='sport|market|side|line|odds|matchup|game_id|teased_odds')
    ap.add_argument('--tease', type=float, default=0.0,
                    help='points to move EVERY leg in the bettor-favourable '
                         'direction. Omit for straight legs / moneylines.')
    ap.add_argument('--odds', type=str, default=None,
                    help='combined American price off the book. Omitted = '
                         'computed from the legs (straight-leg math only).')
    ap.add_argument('--reasoning', default=None)
    ap.add_argument('--sport-scope', default=None)
    ap.add_argument('--rank', type=int, default=None,
                    help='display order, 1 = top of the tab. Default: after '
                         'everything already on the board.')
    ap.add_argument('--edit', default=None, metavar='ID')
    ap.add_argument('--remove', default=None, metavar='ID')
    ap.add_argument('--commit', action='store_true',
                    help='actually write. Default is a dry run.')
    args = ap.parse_args()
    gd = args.date or _et_today()

    if args.list:
        print(f'=== Ledger board · {gd}')
        show(fetch_date(gd))
        return

    if args.remove:
        rows = [r for r in fetch_date(gd) if str(r.get('id')) == str(args.remove)]
        if not rows:
            r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H, timeout=60,
                             params={'select': '*', 'id': f'eq.{args.remove}'})
            rows = r.json() if r.status_code == 200 else []
        if not rows:
            print(f'  no row with id={args.remove}')
            return
        print('  removing:')
        show(rows)
        if rows[0].get('result'):
            print(f'\n  NOTE: this row is graded ({rows[0]["result"]}). Its '
                  f'ledger_snapshots receipt is immutable and stays — deleting '
                  f'here only pulls it off the board.')
        if not args.commit:
            print('\n  DRY RUN — add --commit to remove.')
            return
        d = requests.delete(f'{SB}/rest/v1/{TABLE}', headers=H, timeout=60,
                            params={'id': f'eq.{args.remove}'})
        print(f'  delete -> {d.status_code}')
        return

    if args.edit:
        cur = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H, timeout=60,
                           params={'select': '*', 'id': f'eq.{args.edit}'})
        rows = cur.json() if cur.status_code == 200 else []
        if not rows:
            print(f'  no row with id={args.edit}')
            return
        row = rows[0]
        print('  before:')
        show([row])
        patch = {}
        if args.kind != 'teaser' or row.get('kind') != args.kind:
            if args.kind not in SHIPPED_KINDS:
                print(f'\n  REFUSED: kind {args.kind!r} has no label in the '
                      f'shipped client.\n  Legal: '
                      f'{", ".join(sorted(SHIPPED_KINDS))}')
                return
            if args.kind != 'teaser':
                patch['kind'] = args.kind
        if args.odds is not None:
            patch['combined_odds'] = int(float(args.odds))
        if args.reasoning:
            patch['reasoning'] = args.reasoning
        if args.rank is not None:
            patch['rank'] = args.rank
        if args.leg:
            legs = parse_legs(args.leg, args.tease)
            if legs is None:
                return
            patch['legs'] = legs
            if args.odds is None:
                dec = 1.0
                for lg in legs:
                    dec *= american_to_decimal(lg['original_odds'])
                patch['combined_odds'] = decimal_to_american(dec)
        if not patch:
            print('\n  nothing to change — pass --odds / --reasoning / --leg '
                  '/ --rank / --kind')
            return
        patch['auto_generated'] = False
        if check_snapshot_collision(
                row.get('game_date'),
                patch.get('kind', row.get('kind')),
                row.get('sport_scope'),
                patch.get('combined_odds', row.get('combined_odds')),
                skip_id=args.edit):
            return
        print('\n  patch: ' + json.dumps(patch, default=str)[:700])
        if not args.commit:
            print('\n  DRY RUN — add --commit to write.')
            return
        pr = requests.patch(f'{SB}/rest/v1/{TABLE}', headers=H_W, timeout=60,
                            params={'id': f'eq.{args.edit}'}, json=patch)
        print(f'  patch -> {pr.status_code} {(pr.text or "")[:200]}')
        return

    if not args.leg:
        print('  nothing to do. --list, or --leg to add, or --edit / --remove.')
        return

    if args.kind not in SHIPPED_KINDS:
        print(f'  REFUSED: kind {args.kind!r} has no label in the shipped '
              f'v1.0.1 client — it would render to paying users as the raw '
              f'string {args.kind.upper()}.')
        print(f'  Legal kinds: ' + ', '.join(
            f'{k} ({v})' for k, v in sorted(SHIPPED_KINDS.items())))
        return

    legs = parse_legs(args.leg, args.tease)
    if legs is None:
        return
    if not 2 <= len(legs) <= 5:
        print(f'  REFUSED: {len(legs)} leg(s). The Ledger is a combo surface — '
              f'2-3 legs is the target, 5 the hard cap. A single leg is what '
              f'prime_teased_single was, and that kind was killed 09-24.')
        return

    dec = 1.0
    for lg in legs:
        dec *= american_to_decimal(lg['original_odds'])
    computed = decimal_to_american(dec)
    combined = int(float(args.odds)) if args.odds is not None else computed
    scope = args.sport_scope or (legs[0]['sport'] if len(
        {lg['sport'] for lg in legs}) == 1 else 'MULTI')

    board = fetch_date(gd)
    rank = args.rank if args.rank is not None else (
        max([r.get('rank') or 0 for r in board], default=0) + 1)

    payload = {
        'game_date': gd,
        'kind': args.kind,
        'sport_scope': scope,
        'legs': legs,
        'combined_odds': combined,
        'reasoning': args.reasoning or 'Hand-selected.',
        'rank': rank,
        'auto_generated': False,
    }
    print(f'=== adding to the Ledger board · {gd}')
    show([{**payload, 'id': '(new)', 'result': None}])
    if args.tease:
        print(f'\n  tease: {args.tease:g} pts, favourable direction, every leg '
              f'with a line')
    if args.odds is not None and combined != computed:
        print(f'  price: using your {combined:+d}. Straight-leg math on the '
              f'same legs would be {computed:+d} — expected to differ, a '
              f'teaser is priced off the book\'s table, not the legs.')
    elif args.odds is None:
        print(f'  price: {computed:+d}, computed from the legs. If this is a '
              f'teaser, pass --odds with the book\'s actual price instead.')
    if check_snapshot_collision(gd, args.kind, scope, combined):
        return
    if not args.commit:
        print('\n  DRY RUN — add --commit to write.')
        return
    pr = requests.post(f'{SB}/rest/v1/{TABLE}', headers=H_W, timeout=60,
                       json=payload)
    print(f'  insert -> {pr.status_code} {(pr.text or "")[:220]}')
    if pr.status_code in (200, 201):
        try:
            sid = pr.json()[0]['id']
        except Exception:
            sid = None
        if sid:
            print(f'  id={sid}  (survives pipeline re-runs; snapshot_ledger '
                  f'will record it for grading)')
            # A hand-entered play reaches users the same way a generated one
            # does, so it gets the same immutable publish receipt. Skipping
            # this would leave a manual play with no lock — the exact receipts
            # hole the P0 integrity work closed. Mirrors generate_ledger.py's
            # call: ledger suggestions carry no tier or conviction. Fail-soft;
            # the row is what matters.
            try:
                from prop_publish_lock import lock_publish
                ok = lock_publish(scope, args.kind, sid, None, None, 'ledger')
                print(f'  publish lock -> {"locked" if ok else "FAILED"}')
            except Exception as e:
                print(f'  publish lock -> FAILED ({e})')


if __name__ == '__main__':
    main()
