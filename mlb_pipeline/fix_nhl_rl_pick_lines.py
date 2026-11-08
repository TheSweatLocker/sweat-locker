"""fix_nhl_rl_pick_lines - restore the LINE on any side or total pick
that was resolved before its posted line landed.

(The filename says NHL and rl because that is where it was found. It
now covers five sports and both line-bearing markets - see the market
dispatch note below. Renaming it would mean editing its five call
sites in daily_card.yml for no behavioural gain.)

An NHL rl pick stored primary_play.label 'Buffalo Sabres PL' with
line=None while the SAME ROW carried close_puckline -1.5. The label
builder is not at fault: replayed against the stored row it returns
('Buffalo Sabres -1.5', 'HOME', -1.5) correctly. The pick was resolved
BEFORE the odds landed, and pick_lock then preserved the published
(unlined) primary_play on every later pass, so the gap never healed.

Measured 2026-11-04: 31 of 73 NHL rl picks hold line=None, and 26 of
those are repairable from ctx already stored on their own row.

Why it matters beyond display:
  - receipt_invariants REFUSES a line-bearing market with no pick_line,
    so those picks are published but UNGRADEABLE - 2 of tonight's 4
    Sharp receipts were refused for exactly this.
  - the POTD price path falls back to -110 for a side market. Buffalo
    -1.5 closed at +180. Grading a +180 play at -110 is not a rounding
    error.

This repairs the LINE and LABEL only. The SIDE is never touched - it is
re-derived for the side already stored, so a pick cannot change teams.
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text().split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _pp(row):
    p = row.get('primary_play') or {}
    if isinstance(p, str):
        try: p = json.loads(p)
        except Exception: p = {}
    return p if isinstance(p, dict) else {}


# ══ 2026-11-04 · NHL WAS NOT THE ONLY SPORT ═══════════════════════════
# Measured after the NHL repair: NBA carries the same defect, 8 spread
# picks with line=None, 2 of them repairable from ctx on their own row -
# and both of those are on TODAY's board ('Miami Heat ATS', 'New Orleans
# Pelicans ATS'). A card pick reading 'ATS' with no number is not a play.
#
# Same guarantee as the NHL pass: the SIDE already stored is re-derived,
# never chosen, so a pick cannot change teams.
_TABLE = {'NHL': 'nhl_game_context', 'NBA': 'nba_game_context',
          'NCAAB': 'ncaab_game_context', 'NFL': 'nfl_game_context',
          'NCAAF': 'ncaaf_game_context'}

# ══ 2026-11-07 · THE MARKET IS NOT ONE WORD, AND 'ml' MUST NOT BE TOUCHED ══
# This pass filtered on the single string type == 'rl'. Counted over all
# six ctx tables today, the stored primary_play.type values are:
#
#   ml 710 | rl 864 | total 163 | spread 12 (NFL only)
#
# So it saw neither NFL's own 'spread' rows nor ANY total, and 10 total
# picks sat with line=None - NINE of them on TODAY's board, every one
# published as a bare "Over" / "Under" with no number. That is the exact
# complaint Andy raised about "Knights RL", in a different market.
#
# Dispatch on the MARKET, matched by shape rather than by one spelling.
# 'ml' is deliberately in NEITHER set: a moneyline has no line, so a null
# there is correct and writing a spread number onto one would turn 710
# healthy picks into fabricated plays. My first measuring pass labelled
# those 710 rows "fixable" and they are not - the exclusion is the point.
_SPREAD_TYPES = {'rl', 'spread', 'ats', 'pl', 'puckline', 'runline',
                 'run_line', 'puck_line'}
_TOTAL_TYPES = {'total', 'totals', 'ou', 'over_under', 'over/under'}


def _fmt_line(line: float, side: str) -> str:
    """A total is a LEVEL (6.5); a side is SIGNED (-1.5). Printing a total
    as "+6.5" reads as a spread and would make the log lie about the
    market."""
    return f'{line:g}' if str(side).upper() in ('OVER', 'UNDER') else f'{line:+g}'


def run(date: str | None, dry_run: bool, sport: str = 'NHL') -> int:
    from ensemble_scorer import _label_from_candidate

    sport = str(sport).upper()
    tbl = _TABLE.get(sport)
    if not tbl:
        print(f'  unknown sport {sport!r} - known: {sorted(_TABLE)}')
        return 2
    params = {'select': '*', 'order': 'game_date.asc', 'limit': '1000'}
    if date:
        params['game_date'] = f'eq.{date}'
    rows, off = [], 0
    while True:
        p = dict(params); p['offset'] = str(off)
        chunk = requests.get(f'{SB}/rest/v1/{tbl}',
                             headers=H_R, params=p, timeout=30).json()
        if not isinstance(chunk, list):
            print(f'  fetch failed: {str(chunk)[:200]}'); return 2
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000

    cand = []
    for r in rows:
        pp = _pp(r)
        ty = str(pp.get('type') or '').lower()
        if pp.get('line') is not None: continue
        side = str(pp.get('side') or '').upper()
        if ty in _SPREAD_TYPES:
            if side not in ('HOME', 'AWAY'):
                print(f'  skip {r.get("game_date")} {r.get("home_team")}: '
                      f'{ty} pick with no stored side ({side!r}) - the side '
                      f'is not ours to guess')
                continue
            cand_name = 'HOME_RL' if side == 'HOME' else 'AWAY_RL'
        elif ty in _TOTAL_TYPES:
            if side not in ('OVER', 'UNDER'):
                print(f'  skip {r.get("game_date")} {r.get("home_team")}: '
                      f'{ty} pick with no stored side ({side!r}) - the side '
                      f'is not ours to guess')
                continue
            cand_name = side
        else:
            continue              # 'ml' and anything unmapped: no line to hold
        # _label_from_candidate reads the POSTED line only - close_spread /
        # close_puckline for a side, close_total for a total. It never reads
        # projected_total, and that is the load-bearing safety property here:
        # 6 NHL and 1 NCAAB total picks have NO close_total and only a model
        # projection, so they come back line=None and are left alone. A
        # projection is not a posted line, and filling one in would invent a
        # price to grade against.
        lbl, sd, line = _label_from_candidate(cand_name, r, sport=sport)
        if line is None:
            continue              # no POSTED line on the row - nothing to restore
        cand.append((r, pp, lbl, sd, line))

    print(f'  {sport} rows={len(rows)}  side/total picks with no line and a '
          f'posted one on their own row={len(cand)}')
    fixed = 0
    for r, pp, lbl, sd, line in cand:
        new = dict(pp)
        new['line'] = line
        new['label'] = lbl
        # Stamp the repair so a later audit can tell a restored line from
        # one the resolver wrote itself.
        new['_line_restored_from_ctx'] = True
        print(f'    {r["game_date"]} {str(r.get("away_team"))[:16]:16s} @ '
              f'{str(r.get("home_team"))[:16]:16s}  '
              f'{pp.get("label")!r} -> {lbl!r}  '
              f'(line {_fmt_line(line, sd)}, side {sd})')
        if dry_run:
            continue
        pr = requests.patch(
            f'{SB}/rest/v1/{tbl}', headers=H_W, timeout=20,
            params={'game_id': f'eq.{r["game_id"]}'},
            json={'primary_play': new})
        if pr.status_code not in (200, 204):
            print(f'      PATCH failed {pr.status_code}: {pr.text[:140]}')
            continue
        # A 204 is not a write.
        back = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_R,
                            timeout=20,
                            params={'game_id': f'eq.{r["game_id"]}',
                                    'select': 'primary_play'}).json() or []
        bp = _pp(back[0]) if back else {}
        if bp.get('line') is None:
            print('      NOT confirmed on readback - line still null '
                  '(frozen column?)')
            continue
        fixed += 1
    print(f'  {"[DRY] would fix" if dry_run else "fixed"}: '
          f'{len(cand) if dry_run else fixed} of {len(cand)}')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='one game_date, else all')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--sport', default='NHL',
                    help='NHL (default), NBA, NCAAB, NFL, NCAAF')
    a = ap.parse_args()
    print(f'=== fix_rl_pick_lines · {a.sport.upper()} · {a.date or "ALL"} · '
          f'{"DRY" if a.dry_run else "APPLY"} ===')
    sys.exit(run(a.date, a.dry_run, a.sport))


if __name__ == '__main__':
    main()
