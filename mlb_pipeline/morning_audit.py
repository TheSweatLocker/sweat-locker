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
    """Today's ET date, DST-correct.

    2026-11-14: this was `(now_utc - timedelta(hours=4)).date()`. A fixed -4
    disagrees with true ET in exactly one hour — 04:00-04:59 UTC, on the 127
    EST days a year — where it reads a DAY AHEAD, and it is byte-identical
    the rest of the time. That is not an idle hour: this audit is the thing
    read to decide whether the overnight worked, and mlb_grade_overnight's
    own crons land in it, so an audit run then would grade the state of
    TOMORROW against today's slate and report every surface missing
    ([[feedback_hardcoded_et_offset_breaks_in_est]],
    [[feedback_window_from_runner_clock_is_a_day_ahead]]).

    sweat_dates is the one place the offset is derived; the fallback errs to
    the previous ET day, which is the safe direction for an audit.
    """
    try:
        from sweat_dates import et_today
        return et_today()
    except Exception:                                        # noqa: BLE001
        return (dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(hours=5)).date().isoformat()


#: When each home-page surface is actually BUILT, as UTC HH:MM, measured
#: from jerry_cache.created_at over 2026-11-06..11-13 rather than guessed:
#:
#:   best_bet_*    12:41 .. 13:44 UTC   (POTD)
#:   sharp_card_*  12:57 .. 14:46 UTC
#:
#: Run before that, "MISSING" means NOT BUILT YET, which is not the same
#: finding as "the build failed" — and this audit's own docstring says it is
#: meant for ~8am ET. Run at 04:11 ET it reported four CRITICALs and exit 2
#: for a day whose builds were still hours away. A check that is red every
#: night is a check nobody reads
#: ([[feedback_a_detector_must_know_the_intentional_case]]).
#:
#: The grace is wide on purpose: nba_pipeline.yml's own comment measures a
#: 09:00 ET cron landing at "~1:24pm ET at median lateness", so GitHub cron
#: lateness of several hours is normal here.
SURFACE_BUILD_UTC = {
    'POTD':        '13:45',
    'Sharp Card':  '14:45',
    'Dawg':        '14:45',
    'Daily Degen': '14:45',
}
SURFACE_GRACE_MIN = 120


def _surface_overdue(label: str) -> tuple[bool, str]:
    """(is this surface late, note) — for a surface missing on TODAY."""
    hhmm = SURFACE_BUILD_UTC.get(label)
    if not hhmm:
        return True, ''
    now = dt.datetime.now(dt.timezone.utc)
    hh, mm = (int(x) for x in hhmm.split(':'))
    due = (now.replace(hour=hh, minute=mm, second=0, microsecond=0)
           + dt.timedelta(minutes=SURFACE_GRACE_MIN))
    if now < due:
        mins = int((due - now).total_seconds() // 60)
        return False, f'not built yet — due in {mins // 60}h{mins % 60:02d}m'
    return True, f'overdue (builds by {hhmm}Z +{SURFACE_GRACE_MIN}m)'


def _run_script(path: Path, args: list = None,
                timeout: int = 120) -> tuple[int, str]:
    """Run a script, return (exit_code, output). UTF-8 with replace to
    survive Windows cp1252 decode issues on emoji-heavy child output.

    `timeout` is per-script because daily_performance_report walks every
    surface's full pick history (prop_coverage alone is ~29k rows) and takes
    ~100s even with the pickers running concurrently. At the old flat 120s it
    printed "(timed out)", which is the one output a completeness gate must
    never produce: it is indistinguishable from a clean night.
    """
    try:
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        r = subprocess.run(['python', str(path)] + (args or []),
                           capture_output=True, timeout=timeout,
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


def _pipeline_freshness() -> tuple:
    """Did each sport's OWN pipeline actually fire, and will GitHub run it?

    == 2026-11-08 . WHY THIS EXISTS ==================================
    nba_pipeline.yml and nhl_pipeline.yml fired ZERO runs from 11-06 to
    11-08 and three morning audits missed it, because this audit checked
    SURFACES and never PIPELINES. NBA still had 7 context rows and NHL 6 --
    written by `pregame_lines (NBA + NHL)` and `daily_card`, which do run --
    so every surface check passed on scores three days stale, and the
    incoherent `Boston Celtics -12.5` POTD came out of a scorer whose last
    run was 11-05.

    The cause was a duplicate `continue-on-error` key: a step was inserted
    INSIDE another, so GitHub refused the file and de-registered the
    workflow, and no cron fired at all. `yaml.safe_load` ACCEPTS duplicate
    keys and printed "PARSE OK" on both files that same morning -- so this
    checks the files with a STRICT loader, not a permissive one.

    Two questions, in the order that matters:
      1. Will GitHub even register the file? (strict parse; a duplicate key
         de-registers it silently)
      2. Did the workflow actually fire inside its own window?
         (`workflow_heartbeat`, which is written by the run itself)

    Returns (lines, rc): rc 2 if a pipeline is dark or a file is invalid,
    1 for a soft warning, 0 when healthy.
    """
    out, rc = [], 0

    # ---- 1. would GitHub register every workflow file? ----
    wf_dir = Path(__file__).resolve().parent.parent / '.github' / 'workflows'
    validator = wf_dir.parent / 'scripts' / 'validate_workflows.py'
    if validator.exists():
        _code, txt = _run_script(validator, [])
        bad = [ln for ln in txt.splitlines() if ln.startswith('FAIL')]
        if bad:
            rc = 2
            out.append('   GITHUB WOULD REFUSE %d WORKFLOW FILE(S) -- '
                       'no cron fires for these:' % len(bad))
            for ln in txt.splitlines():
                if ln.startswith('FAIL') or ln.startswith('        '):
                    out.append('     %s' % ln.strip())
            out.append('     (a refused file is listed in the Actions '
                       'sidebar by PATH, not by its name:)')
        else:
            n_all = len(list(wf_dir.glob('*.y*ml')))
            out.append('   workflow files: %d/%d valid for GitHub'
                       % (n_all, n_all))
    else:
        rc = max(rc, 1)
        out.append('   !! validate_workflows.py missing -- cannot prove '
                   'GitHub will register the workflows')

    # ---- 2. did each pipeline actually fire? ----
    # (workflow, label, max age in hours). Weekly sports get a week-wide
    # window; the daily ones allow one missed slot plus the measured +4.5h
    # median cron lateness.
    WATCH = [
        ('mlb_pipeline',   'MLB/shd', 30),
        ('nba_pipeline',   'NBA',     30),
        ('nhl_pipeline',   'NHL',     30),
        ('ncaab_pipeline', 'NCAAB',   30),
        ('nfl_pipeline',   'NFL',    180),
        ('ncaaf_pipeline', 'NCAAF',  192),
        ('ufc_pipeline',   'UFC',    192),
    ]
    now = dt.datetime.now(dt.timezone.utc)
    out.append('   last run per pipeline (from workflow_heartbeat):')
    for wf, label, max_age_h in WATCH:
        rows = _get_json('workflow_heartbeat', {
            'select': 'fired_at,event', 'workflow': 'eq.%s' % wf,
            'order': 'fired_at.desc', 'limit': '1'})
        if not rows:
            rc = 2
            out.append('     %-7s %-15s NEVER -- no heartbeat row at all'
                       % (label, wf))
            continue
        stamp = str(rows[0].get('fired_at') or '')
        try:
            fired = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            age_h = (now - fired).total_seconds() / 3600.0
        except Exception:                                      # noqa: BLE001
            rc = max(rc, 1)
            out.append('     %-7s %-15s unparseable fired_at %r'
                       % (label, wf, stamp))
            continue
        if age_h > max_age_h:
            rc = 2
            flag = 'DARK -- %.0fh ago, window is %dh' % (age_h, max_age_h)
        elif age_h > max_age_h * 0.75:
            rc = max(rc, 1)
            flag = 'late -- %.0fh ago (window %dh)' % (age_h, max_age_h)
        else:
            flag = 'ok -- %.0fh ago' % age_h
        out.append('     %-7s %-15s %s  [%s]'
                   % (label, wf, stamp[:19], flag))
    if rc >= 2:
        out.append('   A DARK PIPELINE STILL LEAVES ROWS ON THE BOARD: '
                   'pregame_lines and daily_card keep writing, so the '
                   'surfaces look populated while the SCORES go stale.')
    return out, rc

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
    _today_et = _et_today()
    for k, v in collect_today_headlines(date).items():
        if 'MISSING' not in v:
            print(f'  ✅ {k:14} {v}')
            continue
        # Only TODAY's surfaces get the benefit of the clock — a past date
        # that is missing one is missing it for good. See SURFACE_BUILD_UTC.
        late, note = ((True, '') if date != _today_et
                      else _surface_overdue(k))
        icon = '🚨' if late else '·'
        suffix = f'  ({note})' if note else ''
        print(f'  {icon} {k:14} {v}{suffix}')

    # ══ 2026-11-14 · LAST NIGHT, BEFORE THE 30-DAY BOARD ═══════════════
    # Andy: "we should be assessing day to day performance across every
    # surface in every sport". A 30-day window structurally cannot show
    # that — one bad night moves a 129-pick denominator by under a point —
    # and the completeness gate below is the part no record surface could
    # ever report, because every picker in compute_surface_records filters
    # `result=not.is.null`. An ungraded pick is invisible to all of them, so
    # a half-graded night reads as a THIN night rather than a broken one.
    #
    # Shown FIRST because it decides whether the 30-day board underneath it
    # is complete. --no-trend keeps the cadence fast; run the script
    # directly for the d7/d30 columns and the leak board.
    print('\n🌙 LAST NIGHT — every surface, every sport:')
    rc_day, out_day = _run_script(here / 'daily_performance_report.py',
                                  ['--no-trend'], timeout=300)
    for line in (out_day or '').strip().split('\n'):
        if line.strip():
            print(f'  {line}')
    if rc_day >= 2:
        print('  🚨 CRITICAL — picks are ungraded and their finals ARE in')
    elif rc_day == 1:
        print('  ⚠️ provisional — still waiting on finals, numbers will move')

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
    print()
    print("PIPELINE FRESHNESS - did each sport's own pipeline fire?")
    try:
        _pf_lines, _pf_rc = _pipeline_freshness()
        for _line in _pf_lines:
            print(_line)
        rc3 = max(rc3, _pf_rc)
    except Exception as _e:                                    # noqa: BLE001
        print('   (freshness check unavailable: %s)' % _e)

    print('\n🖐  HAND-CURATION — POTD + SWEAT CARD (before the noon ET lock)')
    try:
        _cur = _curation_checklist(date)
        for _line in _cur:
            print(_line)
    except Exception as _e:                                    # noqa: BLE001
        print(f'   (checklist unavailable: {_e})')

    # rc_day carries the daily report: 2 = picks ungraded with their finals
    # already in, 1 = still waiting on finals. Both belong in the exit code,
    # because a cadence that exits 0 on an ungraded night is exactly how
    # 2026-11-13 looked clean at 04:11 ET with 72 receipts unsettled.
    worst = max(rc, rc2, rc3, rc_day)
    banner = ('🚨 ACTION REQUIRED' if worst >= 2
              else '⚠️ warnings present' if worst == 1
              else '✅ ALL GREEN')
    print(f'\n╚═══ {banner} ═══╝')
    sys.exit(worst)


if __name__ == '__main__':
    main()
