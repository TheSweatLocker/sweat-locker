"""morning_audit — single command for your morning routine.

Runs consistency_watchdog + audit_context_column_fill +
pipeline_smoke_test + prints the state of every user-visible surface. Meant to run at ~8am ET before you open
the app for the day. If everything's green, you know the pipeline
worked overnight. If not, the output tells you WHAT to fix + HOW.

Prints one clean board. Exit 2 = something requires action, 1 = warnings,
0 = clean.

CLI:
  python morning_audit.py              # today
  python morning_audit.py --date YYYY-MM-DD
  python morning_audit.py --json       # machine-readable output
"""
from __future__ import annotations
import argparse, json, os, sys
import datetime as dt
from pathlib import Path
import subprocess
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _et_today() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date().isoformat()


def _run_script(path: Path, args: list = None) -> tuple[int, str]:
    """Run a script, return (exit_code, output). UTF-8 with replace to
    survive Windows cp1252 decode issues on emoji-heavy child output."""
    try:
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        r = subprocess.run(['python', str(path)] + (args or []),
                           capture_output=True, timeout=120,
                           cwd=str(path.parent), env=env)
        stdout = (r.stdout or b'').decode('utf-8', errors='replace')
        stderr = (r.stderr or b'').decode('utf-8', errors='replace')
        out = stdout + (('\n' + stderr) if stderr else '')
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return -1, '(timed out)'
    except Exception as e:
        return -2, f'exception: {e}'


def _get_json(path: str, params: dict = None) -> object:
    """READ from Supabase."""
    r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=params or {}, timeout=15)
    if r.status_code != 200: return None
    try: return r.json()
    except Exception: return None


def _fmt_hit(w: int, l: int) -> str:
    n = w + l
    if n == 0: return '—'
    return f'{100*w/n:.1f}%'


def collect_surface_records(date: str) -> dict:
    """Return {sport: {surface: (wins, losses, pushes, units_net)}}."""
    rows = _get_json('surface_records',
                     {'window_key': 'eq.d30', 'select': 'sport,surface,wins,losses,pushes,units_net'})
    if not rows: return {}
    by_sport: dict = {}
    for row in rows:
        sp = row.get('sport', '?')
        s = row.get('surface', '?')
        by_sport.setdefault(sp, {})[s] = (
            row.get('wins', 0), row.get('losses', 0),
            row.get('pushes', 0), row.get('units_net', 0.0))
    return by_sport


def collect_today_headlines(date: str) -> dict:
    """Pull today's user-visible surface states in one go."""
    out = {}
    # POTD
    potd = _get_json('jerry_cache',
                     {'cache_key': f'eq.best_bet_{date}', 'select': 'data'})
    if potd:
        d = potd[0].get('data') or {}
        if isinstance(d, str):
            try: d = json.loads(d)
            except Exception: d = {}
        if d.get('noPlay'):
            out['POTD'] = 'noPlay (discipline pass)'
        elif d.get('noGames'):
            out['POTD'] = 'noGames'
        elif d.get('game'):
            out['POTD'] = d.get('leanDisplay', f"{d['game'].get('away_team','?')} @ {d['game'].get('home_team','?')}")
        else:
            out['POTD'] = 'empty payload'
    else:
        out['POTD'] = 'MISSING'
    # Sharp Card
    sc = _get_json('jerry_cache',
                   {'cache_key': f'eq.sharp_card_{date}', 'select': 'data,fetched_at'})
    if sc:
        blob = sc[0].get('data') or {}
        if isinstance(blob, str):
            try: blob = json.loads(blob)
            except Exception: blob = {}
        items = blob.get('items') or []
        out['Sharp Card'] = f'{len(items)} items (locked {sc[0]["fetched_at"][:19]})'
    else:
        out['Sharp Card'] = 'MISSING'
    # Dawg
    dawg = _get_json('daily_dawg',
                     {'game_date': f'eq.{date}',
                      'select': 'team,tier,conviction'})
    if dawg:
        d = dawg[0]
        out['Dawg'] = f"{d.get('team','?')} · {d.get('tier','?')} conv {d.get('conviction','?')}"
    else:
        out['Dawg'] = 'MISSING'
    # Daily Degen
    dd = _get_json('daily_degen',
                   {'game_date': f'eq.{date}', 'select': 'legs'})
    if dd:
        legs = dd[0].get('legs') or []
        out['Daily Degen'] = f'{len(legs) if isinstance(legs, list) else "?"} legs'
    else:
        out['Daily Degen'] = 'MISSING'
    # Slate size
    ctx = _get_json('mlb_game_context',
                    {'game_date': f'eq.{date}', 'select': 'count'})
    if ctx and isinstance(ctx, list):
        out['MLB Slate'] = f'{ctx[0].get("count", 0)} games'
    return out


#: POTD price bands and their measured performance, for the morning
#: checklist. n is small on two of the three, so these are flags for a
#: human, never an automatic veto.
_POTD_BAND_NOTES = (
    (0, 99999, 'plus money', '+16.6% ROI on n=11 — the best band we have'),
    (-150, 0, '-100..-150', 'flat: -0.5% ROI on n=119, the only n>=30 band'),
    (-250, -150, '-150..-250',
     'LOSING: 55.6% against a 64.6% break-even, -14.1% ROI on n=27'),
    (-99999, -250, 'worse than -250', 'past the documented POTD juice gate'),
)


def _curation_checklist(date: str) -> list:
    """What a human should look at on the POTD and the card before noon ET.

    Reads the two published surfaces and flags the specific shapes this
    repo has measured as bad: the -150..-250 POTD band, a tier with no
    conviction behind it, a card concentrated in one sport, and a price we
    published worse than the consensus we had.
    """
    out = []
    recs = _get_json('public_receipts', {
        'select': 'surface,market,sport,pick_label,pick_odds,tier,conviction,result',
        'game_date': f'eq.{date}',
        'surface': 'in.(potd,sweat_card)', 'limit': '60'}) or []
    if not isinstance(recs, list):
        return [f'   receipt fetch failed: {str(recs)[:120]}']
    if not recs:
        return ['   no POTD or Sweat Card receipts yet for this date']
    potd = [x for x in recs if x.get('surface') == 'potd']
    card = [x for x in recs if x.get('surface') == 'sweat_card']

    def _band(o):
        try:
            v = float(o)
        except (TypeError, ValueError):
            return None, 'NO PRICE — cannot be graded for ROI'
        for lo, hi, lbl, note in _POTD_BAND_NOTES:
            if lo <= v < hi:
                return lbl, note
        return None, ''

    for x in potd:
        lbl, note = _band(x.get('pick_odds'))
        flag = '  <-- REVIEW' if (lbl or '').startswith('-150') or lbl is None else ''
        out.append(f"   POTD  {str(x.get('pick_label'))[:38]:38s} "
                   f"{str(x.get('pick_odds')):>6s}  [{lbl or '?'}] {note}{flag}")
    if not potd:
        out.append('   POTD  none published yet')

    # a tier with no conviction behind it
    _noconv = [x for x in card
               if str(x.get('tier') or '').upper() in ('PRIME', 'STRONG')
               and x.get('conviction') is None]
    if _noconv:
        out.append(f'   ⚠ {len(_noconv)} card pick(s) carry a PRIME/STRONG tier with '
                   f'conviction NULL — a tier with no number behind it:')
        for x in _noconv:
            out.append(f'       {str(x.get("pick_label"))[:40]}')

    # one-sport concentration
    if card:
        _by = {}
        for x in card:
            _by[str(x.get('sport'))] = _by.get(str(x.get('sport')), 0) + 1
        _top = max(_by, key=_by.get)
        if _by[_top] == len(card) and len(card) >= 4:
            out.append(f'   ⚠ all {len(card)} card slots are {_top}. Concentrating is '
                       f'correct when one sport holds the best plays — confirm that '
                       f'is the case rather than a composition artefact.')
        out.append(f'   card sports: {_by}')

    # heavy-favourite moneylines, the documented trap
    _heavy = [x for x in card
              if str(x.get('market') or '').lower() == 'ml'
              and x.get('pick_odds') is not None
              and float(x['pick_odds']) <= -200]
    if _heavy:
        out.append(f'   ⚠ {len(_heavy)} card moneyline(s) at -200 or worse '
                   f'(CLAUDE.md names -200+ ML a trap):')
        for x in _heavy:
            out.append(f"       {str(x.get('pick_label'))[:34]:34s} "
                       f"{int(float(x['pick_odds'])):+d}")

    # ══ 2026-11-08 · AN UNRESOLVED PLACEHOLDER IS USER-VISIBLE ═════════
    # signal_expr.render_prose fills {key} from the context row and turns
    # anything it cannot resolve into a literal '?', silently. So a prose
    # template naming a column that does not exist ships a broken chip to
    # users and nothing anywhere complains.
    #
    # Found today on `nfl_home_qb_recent_home_hot`, whose template asked
    # for {home_qb_recent_home_wins} -- not a column, and never was;
    # nfl_game_context carries home_qb_recent_home_win_pct and
    # home_qb_recent_home_starts. Users read
    # "Home QB Drake Maye ?-15 last home starts" on 4 of 13 games on an
    # NFL Sunday, 9 chips since 11-01. The signal's CONDITION was correct
    # throughout, so it fired right and looked broken doing it.
    #
    # Checked the LIVE rendered chips rather than the templates: a static
    # scan of signal_sources.display_prose_template against context
    # columns flagged 60 uses, but most of those placeholders are injected
    # at render time from the pick/prop dict and resolve fine. The rendered
    # text is the only thing that proves a break
    # (feedback_coverage_pct_hides_dead_code).
    _broken_prose = {}
    for _sp, _tbl in (('NFL', 'nfl_game_context'),
                      ('NCAAF', 'ncaaf_game_context'),
                      ('NBA', 'nba_game_context'),
                      ('NHL', 'nhl_game_context'),
                      ('NCAAB', 'ncaab_game_context')):
        try:
            _rows = _get_json(_tbl, {'select': 'primary_play',
                                     'game_date': f'eq.{date}',
                                     'limit': '400'}) or []
        except Exception:                                      # noqa: BLE001
            continue
        for _r in _rows:
            _pp = _r.get('primary_play') or {}
            for _e in (_pp.get('_ensemble_sources') or []):
                if not isinstance(_e, dict):
                    continue
                if '?' in str(_e.get('prose') or ''):
                    _k = (_sp, str(_e.get('signal_key')))
                    _broken_prose[_k] = _broken_prose.get(_k, 0) + 1
    if _broken_prose:
        out.append(f'   ⚠ {sum(_broken_prose.values())} signal chip(s) render '
                   f'an unresolved placeholder as "?" — users see it:')
        for (_sp, _sk), _n in sorted(_broken_prose.items(),
                                     key=lambda kv: -kv[1])[:6]:
            out.append(f'       {_sp:6s} {_sk:40s} {_n} chip(s)')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    date = args.date or _et_today()
    here = Path(__file__).parent

    print(f'╔═══ MORNING AUDIT · {date} ═══╗\n')

    # Headlines — what the user will see today
    print('📱 TODAY (user surfaces):')
    for k, v in collect_today_headlines(date).items():
        icon = '✅' if 'MISSING' not in v else '🚨'
        print(f'  {icon} {k:14} {v}')

    # 30-day records — the receipts
    print('\n📊 30-DAY RECORDS:')
    records = collect_surface_records(date)
    for sport in ['ALL', 'MLB', 'NCAAF', 'NFL', 'NBA', 'NHL', 'NCAAB']:
        surfaces = records.get(sport)
        if not surfaces: continue
        print(f'  {sport}:')
        for s in sorted(surfaces):
            w, l, p, u = surfaces[s]
            print(f'    · {s:20} {w:4}-{l:4}-{p:3}  {u:+7.1f}u  hit {_fmt_hit(w,l)}')

    # Consistency watchdog
    print('\n🔍 CONSISTENCY WATCHDOG:')
    rc, out = _run_script(here / 'consistency_watchdog.py', ['--dry-run'])
    for line in (out or '').splitlines():
        s = line.strip()
        if s.startswith('✓') or s.startswith('🚨') or s.startswith('⚠️'):
            print(f'  {s}')
    if rc >= 2: print('  🚨 CRITICAL — see above')

    # ── 2026-11-07 · THE SCAN THE CADENCE WAS MISSING ──────────────────
    # Andy, on the UNC/UConn gaps: "Why didnt cadence catch this?" It did
    # not, because this audit counted nulls on the SURFACES — unpriced
    # reads, null convictions — and never asked, per column, what fraction
    # of each sport's forward slate actually carries a value. The standing
    # rule already required it; it was being applied to reads and props and
    # not to the context tables those reads are built from.
    #
    # It reports two things, because they have different fixes: a column
    # shared code asks every sport for that is ABSENT on one of them (a
    # missing column 400s the WHOLE fetch — that is how the sharp-fade gate
    # sat dead on five sports while exiting 0), and a column that exists but
    # is empty on every forward row of one sport while populated on another.
    print('\n🧮 CONTEXT COLUMN FILL:')
    rc3, out3 = _run_script(here / 'audit_context_column_fill.py',
                            ['--contrast-limit', '6'])
    for line in (out3 or '').splitlines():
        t = line.strip()
        if t.startswith('🚨') or t.startswith('✅') or t.startswith('·') \
                or 'COLUMN ABSENT' in t or 'ZERO on' in t or 'THIN on' in t \
                or 'no forward rows' in t:
            print(f'  {t}')
    if rc3 >= 2:
        print('  🚨 CRITICAL — a shared column is absent or dark on a live sport')

    # Smoke test
    print('\n🩺 SMOKE TEST:')
    rc2, out2 = _run_script(here / 'pipeline_smoke_test.py')
    for line in (out2 or '').splitlines():
        s = line.strip()
        if s.startswith('✅') or s.startswith('🚨') or s.startswith('⚠️'):
            print(f'  {s}')
    if rc2 >= 2: print('  🚨 CRITICAL — see above')

    # Final status
    # ══ 2026-11-08 · HAND-CURATE THE POTD AND THE CARD, EVERY MORNING ══
    # ANDY: "i think we need to add to cadence every morning, on use hand
    # curating the POTD and sweat card beacause POTDs are not grat right
    # now".
    #
    # He is right, and the number says so. POTD all time is 86W-72L =
    # 54.4% against a 55.3% average break-even, so -2.19 units and -1.4%
    # ROI, and the last 30 days is 9W-11L = 45.0% against 54.0% (-17.3%).
    #
    # The band that does the damage is specific:
    #
    #     price band        n    W-L     hit%    BE%   hit-BE     ROI
    #     plus money       11    6-5    54.5%  45.7%    +8.8   +16.6%
    #     -100..-150      119   64-55   53.8%  54.0%    -0.2    -0.5%
    #     -150..-250       27   15-12   55.6%  64.6%    -9.0   -14.1%
    #
    # The -150..-250 band needs 64.6% and delivers 55.6%. That is where
    # 11-07's POTD lived: Oregon State ML at -225, conviction 79 against an
    # 11-book implied 68.2%. The reasoning was coherent (SP+ 6.6 vs 1.5,
    # offense 94th pct vs defense 12th pct, projected +7.59); the PRICE
    # BAND is the problem, and n=27 there is just under the n>=30 gate, so
    # it is a standing review item rather than an automatic veto.
    #
    # By sport, priced: MLB n=142 at +2.0% is the only positive with
    # volume; NFL 0-5, NHL 0-2, NCAAF 1-2. The POTD was an MLB product and
    # MLB ended 10-28.
    #
    # This block does not decide anything. It puts the two surfaces and the
    # specific traps in front of a human before the noon hard lock, which
    # is the last moment either can be changed.
    print('\n🖐  HAND-CURATION — POTD + SWEAT CARD (before the noon ET lock)')
    try:
        _cur = _curation_checklist(date)
        for _line in _cur:
            print(_line)
    except Exception as _e:                                    # noqa: BLE001
        print(f'   (checklist unavailable: {_e})')

    worst = max(rc, rc2, rc3)
    banner = ('🚨 ACTION REQUIRED' if worst >= 2
              else '⚠️ warnings present' if worst == 1
              else '✅ ALL GREEN')
    print(f'\n╚═══ {banner} ═══╝')
    sys.exit(worst)


if __name__ == '__main__':
    main()
