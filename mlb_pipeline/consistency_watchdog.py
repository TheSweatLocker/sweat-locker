"""consistency_watchdog — cross-table sanity checks that STOP shipping inconsistent data.

Every user-reported bug in the 2026-09-08 session was "two sources of
truth for the same fact":
  - POTD result in jerry_cache vs daily_best_bet_history
  - LEAN props graded in mlb_pipeline_props vs surface_records
  - NCAAF record in sharp_card vs ncaaf_sides
  - Prop graph data in mlb_pipeline_props vs prop_jerry_reads
  - Badge/detail drift: primary_play vs jerry_reads.call_*
  - LR shadow stale vs LIVE recompute

Point-fixes don't scale. This watchdog is the systemic answer: cron
runs it every 30 min, it queries the tables directly, compares them
across the known consistency contracts, and fails LOUD if any check
trips. Exit code 2 = CRITICAL (blocks Sharp Card publish), 1 = WARNING,
0 = clean.

Writes tripped alerts to watchdog_alerts (same table as watchdogs.py).

CLI:
  python consistency_watchdog.py                    # today, all checks
  python consistency_watchdog.py --date 2026-09-08
  python consistency_watchdog.py --check potd_grade_mirror  # single check
  python consistency_watchdog.py --dry-run          # print + skip DB writes
"""
from __future__ import annotations
import argparse, json, os, sys
import datetime as dt
from pathlib import Path
from typing import Optional
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
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}


def _et_today() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date().isoformat()


def _days_ago(n: int) -> str:
    return ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()
            - dt.timedelta(days=n)).isoformat()


# ═══════════════════════════════════════════════════════════════════════
# CHECKS — each returns dict when tripped, None when clean.
# ═══════════════════════════════════════════════════════════════════════

def check_potd_grade_mirror(date: str) -> Optional[dict]:
    """jerry_cache POTD state must match daily_best_bet_history for
    both graded and noPlay days.

    Both are read by different app surfaces (RPC vs calendar). If they
    disagree, one shows Win while another shows Pending — or one shows
    noPlay while another still shows an old pick. User loses trust.

    2026-09-09 EXPANDED: was only checking Win/Loss/Push grade mirror.
    Live audit today caught jerry_cache.noPlay while daily_best_bet_history
    still had a stale pipelineGenerated Marlins pick. Now checks noPlay
    state mirror too.
    """
    since = _days_ago(14)
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_R,
        params={'cache_key': f'like.best_bet_2026-%',
                'select': 'cache_key,data', 'limit': '30',
                'order': 'cache_key.desc'},
        timeout=15,
    )
    if r.status_code != 200: return None
    jc_state_by_date: dict = {}  # date → 'Win' | 'Loss' | 'Push' | 'Void' | 'NoPlay' | 'Live'
    for row in r.json() or []:
        ck = row.get('cache_key') or ''
        if '_mlb' in ck or '_nfl' in ck: continue
        d = ck.replace('best_bet_', '')
        if d < since: continue
        data = row.get('data') or {}
        if isinstance(data, str):
            try: data = json.loads(data)
            except Exception: data = {}
        if data.get('noPlay'):
            jc_state_by_date[d] = 'NoPlay'
        else:
            res = data.get('result')
            if res in ('Win', 'Loss', 'Push', 'Void'):
                jc_state_by_date[d] = res
            elif data.get('game'):
                jc_state_by_date[d] = 'Live'  # pick exists, not yet graded

    if not jc_state_by_date: return None

    r2 = requests.get(
        f'{SB}/rest/v1/daily_best_bet_history',
        headers=H_R,
        params={'bet_date': f'gte.{since}',
                'select': 'bet_date,result,lean', 'limit': '30'},
        timeout=15,
    )
    hist_by_date: dict = {}
    for row in (r2.json() if r2.status_code == 200 else []) or []:
        hist_by_date[row['bet_date']] = {
            'result': (row.get('result') or ''),
            'lean': (row.get('lean') or ''),
        }

    mismatches = []
    for d, jc_state in jc_state_by_date.items():
        hist = hist_by_date.get(d, {'result': 'MISSING', 'lean': ''})
        hist_res = hist['result']
        hist_lean = hist['lean']
        # NoPlay in cache but real pick in history = drift
        if jc_state == 'NoPlay':
            if 'no play' not in hist_lean.lower() and hist_res not in ('No Play', 'MISSING'):
                mismatches.append({'date': d, 'jerry_cache': 'NoPlay',
                                   'daily_best_bet_history': f'{hist_res} · {hist_lean[:40]}'})
        elif jc_state in ('Win', 'Loss', 'Push', 'Void'):
            if hist_res != jc_state:
                mismatches.append({'date': d, 'jerry_cache': jc_state,
                                   'daily_best_bet_history': hist_res})

    if not mismatches: return None
    return {
        'check_name': 'potd_grade_mirror',
        'severity': 'WARNING',
        'message': f'{len(mismatches)} POTD date(s) with state mismatch '
                   f'between jerry_cache and daily_best_bet_history. '
                   f'Home / Receipts / calendar will display conflicting values.',
        'detail': {'mismatches': mismatches[:10],
                   'fix': 'python jerry_anchor_potd.py  (writes both surfaces now)'},
    }


def check_sharp_card_props_have_synthesis(date: str) -> Optional[dict]:
    """Every prop that landed on today's Sharp Card must have a
    prop_jerry_reads row so the graph renders on the card."""
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_R,
        params={'cache_key': f'eq.sharp_card_{date}', 'select': 'data'},
        timeout=15,
    )
    if r.status_code != 200 or not r.json(): return None
    blob = r.json()[0].get('data') or {}
    if isinstance(blob, str):
        try: blob = json.loads(blob)
        except Exception: blob = {}
    items = blob.get('items') or []
    prop_items = [it for it in items if (it.get('type') or '').lower() == 'prop']
    if not prop_items: return None

    # For each prop item, check prop_jerry_reads exists
    missing = []
    for it in prop_items:
        player = it.get('player_name') or it.get('pick', '').split(' Over ')[0].split(' Under ')[0].strip()
        prop_type = it.get('prop_type')
        direction = it.get('direction')
        if not player or not prop_type: continue
        params = {'player_name': f'eq.{player}',
                  'prop_type': f'eq.{prop_type}',
                  'game_date': f'eq.{date}',
                  'select': 'id', 'limit': '1'}
        if direction:
            params['direction'] = f'eq.{direction}'
        pr = requests.get(f'{SB}/rest/v1/prop_jerry_reads', headers=H_R,
                          params=params, timeout=10)
        if pr.status_code == 200 and not pr.json():
            missing.append(f'{player} · {prop_type}')

    if not missing: return None
    return {
        'check_name': 'sharp_card_props_synthesis_gap',
        'severity': 'CRITICAL' if len(missing) >= 5 else 'WARNING',
        'message': f'{len(missing)} of {len(prop_items)} Sharp Card props '
                   f'have NO prop_jerry_reads → no graph on card.',
        'detail': {'missing_props': missing[:15], 'sharp_card_prop_count': len(prop_items)},
    }


def _check_badge_drift_for_sport(date: str, sport: str, ctx_table: str) -> list:
    """Return list of drift dicts for one sport. Empty list = clean."""
    r = requests.get(
        f'{SB}/rest/v1/{ctx_table}',
        headers=H_R,
        params={'game_date': f'eq.{date}', 'primary_play': 'not.is.null',
                'select': 'game_id,home_team,away_team,primary_play',
                'limit': '80'},
        timeout=15,
    )
    if r.status_code != 200: return []
    ctx_rows = r.json() or []
    if not ctx_rows: return []
    game_ids = [c['game_id'] for c in ctx_rows if c.get('game_id')]
    if not game_ids: return []

    jr = requests.get(
        f'{SB}/rest/v1/jerry_reads',
        headers=H_R,
        params={'sport': f'eq.{sport}', 'game_date': f'eq.{date}',
                'game_id': f'in.({",".join(game_ids)})',
                'select': 'game_id,call_market,call_side,call_text',
                'limit': '80'},
        timeout=15,
    )
    jr_by_game = {row['game_id']: row for row in (jr.json() if jr.status_code == 200 else []) or []}

    mismatches = []
    for c in ctx_rows:
        gid = c['game_id']
        pp = c.get('primary_play')
        if isinstance(pp, str):
            try: pp = json.loads(pp)
            except Exception: pp = {}
        pp = pp or {}
        jr_row = jr_by_game.get(gid)
        if not jr_row: continue
        pp_type = (pp.get('type') or '').lower()
        pp_side = (pp.get('side') or '').upper()
        jr_type = (jr_row.get('call_market') or '').lower()
        jr_side = (jr_row.get('call_side') or '').upper()
        if pp_type and jr_type and pp_type != jr_type:
            mismatches.append({
                'sport': sport,
                'game': f'{c.get("away_team","?")[:12]} @ {c.get("home_team","?")[:12]}',
                'primary_play': f'{pp_type}/{pp_side}',
                'jerry_reads':  f'{jr_type}/{jr_side}',
            })
    return mismatches


def check_badge_matches_detail(date: str) -> Optional[dict]:
    """For every game_context today with primary_play, jerry_reads.call_*
    must match primary_play (type/side/label). Divergence = badge on
    games tab shows one pick, game detail shows another.

    2026-09-09: extended to check ALL sports with primary_play tables
    (MLB, NFL, NCAAF, NBA, NHL, NCAAB). Was MLB-only before → other
    sports' drift went undetected.
    """
    all_mismatches = []
    SPORT_CTX = [
        ('MLB',   'mlb_game_context'),
        ('NFL',   'nfl_game_context'),
        ('NCAAF', 'ncaaf_game_context'),
        ('NBA',   'nba_game_context'),
        ('NHL',   'nhl_game_context'),
        ('NCAAB', 'ncaab_game_context'),
    ]
    for sport, tbl in SPORT_CTX:
        try:
            all_mismatches.extend(_check_badge_drift_for_sport(date, sport, tbl))
        except Exception:
            continue

    if not all_mismatches: return None
    by_sport = {}
    for m in all_mismatches:
        by_sport.setdefault(m['sport'], 0)
        by_sport[m['sport']] += 1
    sport_summary = ', '.join(f'{s}={n}' for s, n in sorted(by_sport.items()))
    return {
        'check_name': 'badge_detail_drift',
        'severity': 'CRITICAL',
        'message': f'{len(all_mismatches)} game(s) with primary_play vs jerry_reads '
                   f'market mismatch ({sport_summary}). Badges + game detail contradict.',
        'detail': {'mismatches': all_mismatches[:15],
                   'fix_hint': 'python jerry_pick_scrub.py --sport <SPORT> for each'},
    }


def check_lean_prop_rollup(date: str) -> Optional[dict]:
    """LEAN + COVERAGE prop surface_records must exist when graded props exist.
    Otherwise Receipts will show 0-0 for tiers that have real records.
    """
    # Any graded LEAN props last 7 days?
    since = _days_ago(7)
    r = requests.get(
        f'{SB}/rest/v1/mlb_pipeline_props',
        headers=H_R,
        params={'game_date': f'gte.{since}', 'tier': 'eq.LEAN',
                'result': 'in.(Win,Loss,Push,win,loss,push)',
                'select': 'result', 'limit': '5'},
        timeout=15,
    )
    if r.status_code != 200: return None
    if not r.json(): return None  # no graded LEAN props = expected empty

    # Then surface_records must have a prop_lean entry
    sr = requests.get(
        f'{SB}/rest/v1/surface_records',
        headers=H_R,
        params={'surface': 'eq.prop_lean', 'sport': 'eq.MLB',
                'window_key': 'eq.d7',
                'select': 'wins,losses,pushes'},
        timeout=15,
    )
    rows = sr.json() if sr.status_code == 200 else []
    if not rows or (rows[0].get('wins', 0) + rows[0].get('losses', 0)) == 0:
        return {
            'check_name': 'lean_prop_rollup_missing',
            'severity': 'WARNING',
            'message': 'Graded LEAN props exist in mlb_pipeline_props but '
                       'surface_records.prop_lean is empty. '
                       'Receipts LEAN row will show 0-0.',
            'detail': {'fix_hint': 'python compute_surface_records.py'},
        }
    return None


def check_lr_shadow_coverage(date: str) -> Optional[dict]:
    """Every MLB game with primary_play should have _lr_ml_shadow and
    _lr_total_shadow populated. Gap = downstream LR gates silently no-op."""
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context',
        headers=H_R,
        params={'game_date': f'eq.{date}', 'primary_play': 'not.is.null',
                'select': 'primary_play,home_team,away_team', 'limit': '30'},
        timeout=15,
    )
    if r.status_code != 200: return None
    rows = r.json() or []
    if not rows: return None
    ml_ok = tot_ok = 0
    missing = []
    for c in rows:
        pp = c.get('primary_play')
        if isinstance(pp, str):
            try: pp = json.loads(pp)
            except Exception: pp = {}
        pp = pp or {}
        has_ml = bool((pp.get('_lr_ml_shadow') or {}).get('p_home_win'))
        has_tot = bool((pp.get('_lr_total_shadow') or {}).get('p_over'))
        if has_ml: ml_ok += 1
        if has_tot: tot_ok += 1
        if not (has_ml and has_tot):
            missing.append({'game': f'{c.get("away_team","?")[:12]} @ {c.get("home_team","?")[:12]}',
                            'ml': has_ml, 'total': has_tot})
    total = len(rows)
    if ml_ok == total and tot_ok == total: return None
    return {
        'check_name': 'lr_shadow_coverage_gap',
        'severity': 'WARNING' if (ml_ok/total >= 0.8 and tot_ok/total >= 0.8) else 'CRITICAL',
        'message': f'MLB LR shadow coverage: ML {ml_ok}/{total}, Total {tot_ok}/{total}. '
                   f'Games without shadow silently no-op the LR gate.',
        'detail': {'missing_games': missing[:10],
                   'fix_hint': 'python recompute_primary_play.py --force'},
    }


def check_ncaaf_sides_over_sharp_card(date: str) -> Optional[dict]:
    """When NCAAF has both sharp_card AND ncaaf_sides surface records,
    ncaaf_sides is the authoritative fuller record. Warn if only
    sharp_card exists (Receipts would understate NCAAF)."""
    r = requests.get(
        f'{SB}/rest/v1/surface_records',
        headers=H_R,
        params={'sport': 'eq.NCAAF', 'window_key': 'eq.d30',
                'select': 'surface,wins,losses'},
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    if not rows: return None
    surfaces = {row['surface']: row for row in rows}
    sc = surfaces.get('sharp_card')
    ns = surfaces.get('ncaaf_sides')
    if sc and not ns:
        return {
            'check_name': 'ncaaf_sides_surface_missing',
            'severity': 'WARNING',
            'message': 'NCAAF sharp_card record exists but ncaaf_sides '
                       'record missing → Receipts undercounts NCAAF.',
            'detail': {'fix_hint': 'python compute_surface_records.py'},
        }
    return None


def check_prop_direction_flip_drift(date: str) -> Optional[dict]:
    """When tier calibration flips a prop's direction (fade the over →
    back the under per feedback_fade_not_suppress_803), it patches
    prop_jerry_reads BUT NOT mlb_pipeline_props. Sharp Card composer
    reads raw props → shows OVER while Jerry synth says UNDER.

    Alcantara ha_over PRIME 74 (raw) → ha_under STRONG (jerry): today's
    live example. User sees Sharp Card 'bet Over' + Jerry read 'fade Over'.

    Flags divergence for v1.0.1 composer refactor. Not auto-fixable
    tonight — composer change is prop-scoring adjacent, needs careful
    test.
    """
    # Get raw props with tier PRIME/STRONG (likely to be on Sharp Card)
    raw = requests.get(
        f'{SB}/rest/v1/mlb_pipeline_props',
        headers=H_R,
        params={'game_date': f'eq.{date}',
                'tier': 'in.(PRIME,STRONG)',
                'select': 'player_name,prop_type,direction',
                'limit': '200'},
        timeout=15,
    )
    if raw.status_code != 200: return None
    raw_props = raw.json() or []
    if not raw_props: return None

    # For each, check if prop_jerry_reads has a FLIPPED direction
    flips = []
    for p in raw_props:
        player = p.get('player_name')
        raw_dir = (p.get('direction') or '').lower()
        raw_pt = (p.get('prop_type') or '').lower()
        if not player or not raw_dir: continue
        # Fetch jerry_reads for same player+family
        family = raw_pt.rsplit('_', 1)[0] if '_' in raw_pt else raw_pt
        opp_dir = 'under' if raw_dir == 'over' else 'over'
        opp_pt = f'{family}_{opp_dir}'
        pr = requests.get(
            f'{SB}/rest/v1/prop_jerry_reads',
            headers=H_R,
            params={'sport': 'eq.MLB', 'game_date': f'eq.{date}',
                    'player_name': f'eq.{player}',
                    'prop_type': f'eq.{opp_pt}',
                    'select': 'prop_type,direction', 'limit': '1'},
            timeout=10,
        )
        if pr.status_code == 200 and pr.json():
            flips.append(f'{player} · raw={raw_pt} → synth={opp_pt}')

    if not flips: return None
    return {
        'check_name': 'prop_direction_flip_drift',
        'severity': 'WARNING',
        'message': f'{len(flips)} prop(s) with raw direction ≠ jerry synth '
                   f'direction (tier calibration flipped, composer '
                   f'shows raw). Sharp Card + Jerry read contradict.',
        'detail': {'examples': flips[:10],
                   'fix_hint': 'v1.0.1 composer refactor OR patch mlb_pipeline_props on calibration flip'},
    }


# ══ 2026-10-21 · THE CARD CONTRADICTED ITSELF AND EVERY GATE PASSED ══
# QA on GSW @ LAL found a card arguing with itself: the headline said Lakers
# -5.5 while the market strip said -6.5, the model tile named Golden State, the
# predicted score favoured Golden State, the Monte Carlo had the Lakers by only
# 2.6 against a -6.5 line, and Jerry wrote that a 62% win probability was
# "clean enough to price out the heavy ML" when -225 implies 69.2%.
#
# Every one of those passed. consistency_watchdog ran the same morning and
# reported 0/7 tripped, because none of its seven checks look at whether a
# published pick agrees with the numbers printed beside it.
#
# These are those checks. Two of them are SIGN-FREE and therefore run on every
# sport; the third needs a spread convention and runs ONLY where that
# convention has been proven from the writer, because a watchdog that is itself
# wrong about a sign is worse than no watchdog.
#
#   A. PRICE vs MODEL  (sign-free, every sport)
#      If we take a side at a price whose implied probability EXCEEDS our own
#      model's win probability for that side, we are laying a number our model
#      says is too short. That is the GSW @ LAL error exactly: 62% model
#      against 69.2% implied. CRITICAL, because it is a direct bet-quality
#      statement and it is unambiguous.
#
#   B. PICK LINE vs MARKET  (sign-free, every sport)
#      Reports the gap between the line we published and the line now on the
#      board, AND which way it runs. This is deliberately a WARNING, not an
#      error: holding -5.5 while the market moves to -6.5 is the pick lock
#      working and is GOOD closing-line value. The defect is that the card
#      shows both numbers and explains neither, so a reader sees a
#      contradiction where there is a held number.
#
#   C. PROJECTION vs PICK SIDE  (needs a sign; NBA and NCAAF only)
#      Whether the model's own projected_spread favours the side we picked.
#      NBA is negative-is-home (nba_elo.elo_to_spread, verified by call);
#      NCAAF is positive-is-home. NFL's context game_ids do not join its
#      results, NHL's writer is unsettled and MLB's retention is 24 rows, so
#      those are SKIPPED by name rather than assumed.

#: projected_spread orientation, from each sport's WRITER. Positive value means
#: "positive = home favoured". Absent = not proven, do not check.
_PROJ_HOME_POSITIVE = {'NCAAF': True, 'NBA': False}

#: close_spread orientation, proven 2026-10-20 by two methods on n=2132-7338.
_CLOSE_HOME_POSITIVE = {'NFL': True, 'NCAAF': False, 'NBA': False, 'MLB': False}

_CTX_TABLE = {'NBA': 'nba_game_context', 'NCAAF': 'ncaaf_game_context',
              'NFL': 'nfl_game_context', 'NHL': 'nhl_game_context',
              'MLB': 'mlb_game_context'}


def _implied(american) -> Optional[float]:
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return (abs(a) / (abs(a) + 100.0)) if a < 0 else (100.0 / (a + 100.0))


def check_card_self_contradiction(date: str) -> Optional[dict]:
    """Published picks that disagree with the numbers printed beside them."""
    problems = []
    for sport, tbl in _CTX_TABLE.items():
        r = requests.get(
            f'{SB}/rest/v1/{tbl}', headers=H_R,
            params={'select': '*', 'game_date': f'eq.{date}', 'limit': '200'},
            timeout=20)
        if r.status_code != 200:
            continue
        for g in (r.json() or []):
            pp = g.get('primary_play')
            if isinstance(pp, str):
                try:
                    pp = json.loads(pp)
                except Exception:                              # noqa: BLE001
                    pp = None
            if not isinstance(pp, dict):
                continue
            side = str(pp.get('side') or '').upper()
            if side not in ('HOME', 'AWAY'):
                continue
            # 2026-10-21 refinement: only judge picks that can REACH a
            # surface. The first run flagged eight, six of them COVERAGE tier
            # — a tier the card never shows. Judging rows nobody publishes is
            # how a check earns a mute.
            if str(pp.get('tier') or '').upper() not in ('PRIME', 'STRONG', 'LEAN'):
                continue
            market = str(pp.get('type') or '').lower()
            match = f"{g.get('away_team')} @ {g.get('home_team')}"
            tier = str(pp.get('tier') or '')

            # ── A. price vs model win probability ──────────────────────
            wp = pp.get('_lr_p_home_win')
            if wp is None:
                lr = pp.get('_lr_ml_shadow') or {}
                wp = lr.get('p_home_win') if isinstance(lr, dict) else None
            # 2026-10-21 refinement: a win probability priced against the
            # MONEYLINE says nothing direct about a SPREAD bet — a team can be
            # a 62% favourite and still be good value at -5.5. The first run
            # fired this on three spread picks, which is apples to oranges and
            # exactly the kind of false positive that gets a gate ignored. So
            # the price test now runs ONLY on moneyline picks, and spreads get
            # the test that actually applies to them (below).
            if wp is not None and market == 'ml':
                try:
                    p_home = float(wp)
                    p_side = p_home if side == 'HOME' else 1.0 - p_home
                    price = (g.get('home_ml_close') if side == 'HOME'
                             else g.get('away_ml_close'))
                    if price is None:
                        price = (g.get('close_home_ml') if side == 'HOME'
                                 else g.get('close_away_ml'))
                    imp = _implied(price)
                    if imp is not None and p_side < imp - 0.03:
                        problems.append({
                            'sport': sport, 'game': match, 'kind': 'PRICE',
                            'tier': tier,
                            'detail': (f'ML pick: model {p_side*100:.0f}% on '
                                       f'{side} vs {imp*100:.0f}% implied by '
                                       f'{price} — laying a price our own '
                                       f'model calls too short'),
                        })
                except (TypeError, ValueError):
                    pass

            # ── A2. SPREAD picks: does our projection cover the line ──
            # The right question for a spread bet, and it needs the same
            # proven sign table as check C.
            hp0 = _PROJ_HOME_POSITIVE.get(sport)
            ps0 = g.get('projected_spread')
            pl0 = pp.get('line')
            if (market in ('rl', 'spread') and hp0 is not None
                    and ps0 is not None and pl0 is not None):
                try:
                    norm = float(ps0) * (1.0 if hp0 else -1.0)   # +ve = home
                    # margin the pick needs: a -6.5 home pick needs home +6.5
                    need = abs(float(pl0))
                    got = norm if side == 'HOME' else -norm
                    # 2026-10-21: require a MEANINGFUL shortfall. The first
                    # run flagged "laying -7.5, projection has 7.3" — a 0.2
                    # gap on a model with multi-point error. Anything under a
                    # point is agreement, not contradiction.
                    if float(pl0) < 0 and (need - got) >= 1.0:
                        problems.append({
                            'sport': sport, 'game': match, 'kind': 'COVER',
                            'tier': tier,
                            'detail': (f'laying {float(pl0):+g} on {side} but '
                                       f'our projection has that side by only '
                                       f'{got:.1f} — our own number does not '
                                       f'cover the line we published'),
                        })
                except (TypeError, ValueError):
                    pass

            # ── B. published line vs the board ─────────────────────────
            pl, cs = pp.get('line'), g.get('close_spread')
            if pl is not None and cs is not None:
                try:
                    # ══ 2026-10-21 · MY OWN CHECK HAD A CONVENTION BUG ══
                    # First refined run reported FLIP on BYU @ UCF, LSU @
                    # Auburn and Iowa @ Minnesota — every one of them an AWAY
                    # pick, and every one a FALSE POSITIVE.
                    #
                    # primary_play.line is written from the PICKED SIDE's
                    # perspective ("LSU -7.5"), while close_spread is always
                    # HOME-perspective. On an away favourite those read as
                    # opposite signs while describing the SAME bet: board
                    # +7.5 means home Auburn is a 7.5 underdog, i.e. LSU -7.5.
                    #
                    # Normalise to the home perspective before comparing. The
                    # irony is not lost: a check written to catch cards that
                    # contradict themselves nearly shipped contradicting
                    # itself, in exactly the side-vs-home convention family it
                    # was built to police.
                    a = float(pl) if side == 'HOME' else -float(pl)
                    # ══ SECOND CONVENTION BUG IN THE SAME CHECK ══
                    # The run after the fix above reported FLIP on five NFL
                    # games. Also false. _CLOSE_HOME_POSITIVE was DEFINED at
                    # the top of this file and never applied: NFL stores
                    # close_spread as a HOME MARGIN (positive = home favoured,
                    # proven 2026-10-20 at MAE 10.26 vs 14.60 and home win%
                    # 67.4 vs 35.4 on n=7338), while every other sport stores
                    # a HANDICAP. `a` above is a handicap, so NFL's board has
                    # to be negated before the two are comparable.
                    #
                    # A sport whose convention is NOT in that table is skipped
                    # rather than assumed — NHL is unproven, and guessing is
                    # what produced both of these false alarms.
                    _chp = _CLOSE_HOME_POSITIVE.get(sport)
                    if _chp is None:
                        raise ValueError('close_spread convention unproven')
                    b = float(cs) * (-1.0 if _chp else 1.0)
                    # 2026-10-21 refinement: do not flag every move. Four of
                    # the first five hits were the lock HOLDING A BETTER
                    # NUMBER, which is good closing-line value and not a
                    # defect. Only two cases deserve a reader's attention:
                    #   * the sign flipped — the published side is no longer
                    #     the side the board describes
                    #   * the board moved far enough that the published number
                    #     is stale rather than favourable
                    if (a < 0) != (b < 0) and abs(a - b) >= 1.0:
                        problems.append({
                            'sport': sport, 'game': match, 'kind': 'FLIP',
                            'tier': tier,
                            'detail': (f'published {a:+g} but the board is '
                                       f'{b:+g} — the SIGN differs, so the '
                                       f'published pick and the board do not '
                                       f'describe the same bet'),
                        })
                    else:
                        # ══ THIRD CONVENTION SLIP, SAME CHECK ══
                        # This compared MAGNITUDES, which is wrong for a dog.
                        # It flagged NFL LA @ LV "published +5.5 vs board
                        # +4.5 — laying MORE than the market asks". Taking
                        # +5.5 when the board offers +4.5 is a BETTER number,
                        # not a worse one. Magnitude only tracks quality for
                        # favourites.
                        #
                        # Compare the PICKED SIDE's own number instead: higher
                        # is always better for whoever holds it, favourite or
                        # dog. Flag only when we hold a WORSE number than the
                        # board currently offers, which is the only version of
                        # this that costs anything.
                        board_side = b if side == 'HOME' else -b
                        if float(pl) < board_side - 1.0:
                            problems.append({
                                'sport': sport, 'game': match,
                                'kind': 'STALE', 'tier': tier,
                                'detail': (f'published {float(pl):+g} on '
                                           f'{side} but the board now offers '
                                           f'{board_side:+g} — we are holding '
                                           f'a WORSE number than is available'),
                            })
                except (TypeError, ValueError):
                    pass

            # ── C. does our own projection favour the side we took ─────
            hp = _PROJ_HOME_POSITIVE.get(sport)
            ps = g.get('projected_spread')
            if hp is not None and ps is not None:
                try:
                    norm = float(ps) * (1.0 if hp else -1.0)   # +ve = home
                    proj_side = 'HOME' if norm > 0 else 'AWAY'
                    if abs(norm) >= 1.0 and proj_side != side:
                        problems.append({
                            'sport': sport, 'game': match, 'kind': 'SIDE',
                            'tier': tier,
                            'detail': (f'pick {side} but projected_spread '
                                       f'favours {proj_side} by '
                                       f'{abs(norm):.1f}'),
                        })
                except (TypeError, ValueError):
                    pass

    if not problems:
        return None
    # FLIP, SIDE, COVER and PRICE are all "the pick argues with its own
    # numbers". STALE is a value complaint, not a contradiction.
    hard = [x for x in problems
            if x['kind'] in ('PRICE', 'SIDE', 'COVER', 'FLIP')]
    kinds = ', '.join(sorted({x['kind'] for x in problems}))
    return {
        'check_name': 'card_self_contradiction',
        # A pick that argues with its own numbers is a publish-quality
        # problem, not a cosmetic one — but a held line on its own is not.
        'severity': 'CRITICAL' if hard else 'WARNING',
        'message': (f'{len(problems)} published pick(s) disagree with the '
                    f'numbers shown beside them ({kinds}).'),
        'detail': {'problems': problems[:25],
                   'fix_hint': 'card_self_contradiction — see the note above '
                               'this check for what each kind means'},
    }


CHECKS = {
    'potd_grade_mirror':           check_potd_grade_mirror,
    'sharp_card_props_synthesis':  check_sharp_card_props_have_synthesis,
    'badge_detail_drift':          check_badge_matches_detail,
    'lean_prop_rollup':            check_lean_prop_rollup,
    'lr_shadow_coverage':          check_lr_shadow_coverage,
    'ncaaf_sides_over_sharp_card': check_ncaaf_sides_over_sharp_card,
    'prop_direction_flip_drift':   check_prop_direction_flip_drift,
    'card_self_contradiction':     check_card_self_contradiction,
}


def write_alert(alert: dict, date: str) -> None:
    """Upsert into watchdog_alerts (same table as watchdogs.py uses)."""
    payload = {
        'run_date': date,
        'check_name': alert['check_name'],
        'severity': alert['severity'],
        'message': alert['message'],
        'detail': alert.get('detail') or {},
        'last_seen_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    r = requests.post(
        f'{SB}/rest/v1/watchdog_alerts?on_conflict=check_name,run_date',
        headers={**H_W, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        json=payload, timeout=10,
    )
    if r.status_code >= 400:
        print(f'  ⚠ alert write failed {r.status_code}: {r.text[:120]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: today ET)')
    ap.add_argument('--check', choices=list(CHECKS),
                    help='Run one specific check')
    ap.add_argument('--dry-run', action='store_true',
                    help='Print alerts but do not persist to DB')
    args = ap.parse_args()

    date = args.date or _et_today()
    to_run = {args.check: CHECKS[args.check]} if args.check else CHECKS

    print(f'=== consistency_watchdog · {date} · {len(to_run)} check(s) '
          f'{"· DRY" if args.dry_run else ""} ===')

    tripped = 0
    critical = 0
    for name, fn in to_run.items():
        try:
            result = fn(date)
        except Exception as e:
            print(f'  ✗ {name}: exception {type(e).__name__}: {e}')
            continue
        if result is None:
            print(f'  ✓ {name}: clean')
            continue
        sev = result.get('severity', '?')
        msg = result.get('message', '?')
        print(f'  {"🚨" if sev == "CRITICAL" else "⚠️"} {sev}  {name}: {msg}')
        tripped += 1
        if sev == 'CRITICAL': critical += 1
        if not args.dry_run:
            write_alert(result, date)

    print(f'\n{"" if not tripped else "⚠️ "}{tripped}/{len(to_run)} checks tripped '
          f'({critical} critical)')

    # Exit codes: 0 clean, 1 warnings, 2 critical (workflow can gate on this)
    sys.exit(2 if critical > 0 else (1 if tripped > 0 else 0))


if __name__ == '__main__':
    main()
