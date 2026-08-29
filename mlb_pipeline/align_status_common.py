"""Sport-agnostic alignment computation.

Given a sport code + game_context table name + optional lens-field map,
compute align_status + oddscrowd_snapshot per game and write back.

The core alignment logic (ext consensus × oc money side → aligned verdict)
is sport-invariant. Only the lens/model fields differ per sport, and
those are optional inputs (used to fill lens_side/lens_count blocks).

Callers:
  compute_align_status.py           (MLB)
  compute_align_status_nfl.py       (NFL — TODO)
  compute_align_status_ncaaf.py     (NCAAF — TODO)
  compute_align_status_ncaab.py     (NCAAB — TODO)

Each caller supplies:
  sport_code    'MLB' | 'NFL' | 'NCAAF' | 'NCAAB'
  context_table 'mlb_game_context' | 'nfl_game_context' | ...
  lens_fields   {'panel': col, 'jerry': col, ...} — model margin columns
"""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _side_from_margin(m):
    if m is None: return None
    return 'H' if m > 0 else ('A' if m < 0 else None)


def _parse_oc(ext_row: dict) -> Optional[dict]:
    conf = ext_row.get('confidence') or ''
    m = re.search(r'money (\d+)%.*?bets (\d+)%.*?div ([+-]?\d+)', conf)
    if not m:
        return None
    return {
        'pick': ext_row.get('pick_side'),
        'money': int(m.group(1)),
        'bets': int(m.group(2)),
        'div': int(m.group(3)),
        'fade': ext_row.get('fade_flag'),
    }


def _verdict(aligned, ext_count, oc_div):
    if aligned is None:
        return 'no_data'
    if not aligned and ext_count >= 3:
        return 'disagreement'
    if aligned:
        if ext_count >= 4 and oc_div is not None and oc_div >= 5:
            return 'aligned_strong'
        if ext_count >= 3:
            return 'aligned'
        return 'aligned_soft'
    return 'no_data'


def load_contexts(sb_url: str, sb_key: str, context_table: str,
                  game_date: str, extra_select: str = '') -> list:
    # 2026-08-06: added open_total/current_total/home_ml_open/close for RLM
    # detector in market_status (_compute_rlm needs these to detect reverse
    # line movement — line moved opposite to public money).
    # 2026-08-26: some sports don't have all RLM columns (NCAAF missing
    # current_total + home_ml_* fields). Try the full SELECT first, then
    # progressively drop optional columns on 42703.
    core_sel = 'game_id,away_team,home_team,close_total,close_spread'
    rlm_sel  = 'open_total,current_total,home_ml_open,home_ml_close,home_ml_odds'
    optional_cols = ['current_total', 'home_ml_open', 'home_ml_close', 'home_ml_odds', 'open_total']
    sel = (core_sel + ',' + rlm_sel + (',' + extra_select if extra_select else ''))
    h = {'apikey': sb_key, 'Authorization': f'Bearer {sb_key}'}

    for attempt in range(len(optional_cols) + 1):
        r = requests.get(
            f'{sb_url}/rest/v1/{context_table}',
            params={'game_date': f'eq.{game_date}', 'select': sel},
            headers=h, timeout=30,
        )
        if r.status_code == 200:
            return r.json()
        # 42703 = column does not exist — drop it and retry
        if r.status_code == 400 and 'does not exist' in (r.text or ''):
            dropped = None
            for col in optional_cols:
                if col in sel and col in (r.text or ''):
                    dropped = col
                    break
            if not dropped: break
            # rebuild sel without that column
            sel = ','.join(x for x in sel.split(',') if x.strip() != dropped)
            continue
        break
    return []


def load_externals(sb_url: str, sb_key: str, sport_code: str, game_date: str) -> dict:
    h = {'apikey': sb_key, 'Authorization': f'Bearer {sb_key}'}
    r = requests.get(
        f'{sb_url}/rest/v1/external_picks',
        params={'sport': f'eq.{sport_code}', 'game_date': f'eq.{game_date}',
                'select': 'game_id,source,surface,pick_side,confidence,fade_flag,pulled_at'},
        headers=h, timeout=30,
    )
    j = r.json() if r.status_code == 200 else []
    out = defaultdict(list)
    for e in j if isinstance(j, list) else []:
        if isinstance(e, dict):
            out[e.get('game_id')].append(e)
    return out


def load_public_splits_snapshot(sb_url: str, sb_key: str, sport_code: str,
                                 game_date: str) -> dict:
    """Load ScoresAndOdds public splits and transform into the same
    {ml/rl/total: {pick, money, bets, fade}} snapshot format that
    OddsCrowd uses. Used as an OC fallback for sports OC doesn't cover
    (NCAAF — user flagged 8/26).

    public_splits_v2 rows are long-form: one per (game, market, side,
    metric). Aggregate into per-game per-market {pick: majority-money side,
    money: majority-money %, bets: majority-money %}.
    Returns dict game_id → {ml, rl, total} snapshot.
    """
    h = {'apikey': sb_key, 'Authorization': f'Bearer {sb_key}'}
    # public_splits_v2 has no game_date column — pulls are stamped with
    # snapshot_ts (when we scraped). Get the last N days of snapshots and
    # filter by game_id downstream. Cast wide net so we catch a snapshot
    # pulled several days before the game.
    from datetime import datetime, timedelta, timezone
    ts_cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    # 2026-08-28: paginate — client `limit=10000` is capped server-side
    # at 1000. Prior version silently dropped later snapshots for late-
    # slate games, then combined with unsorted iteration (also fixed)
    # to serve stale morning splits downstream.
    rows = []
    for off in range(0, 50000, 1000):
        r = requests.get(
            f'{sb_url}/rest/v1/public_splits_v2',
            params={'sport': f'eq.{sport_code}', 'source': 'eq.so',
                    'snapshot_ts': f'gte.{ts_cutoff}',
                    'select': 'game_id,market,side,metric,value,snapshot_ts',
                    'order': 'snapshot_ts.asc',
                    'limit': 1000, 'offset': off},
            headers=h, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list): return {}
        rows.extend(chunk)
        if len(chunk) < 1000: break

    # game_id → market → side → {bets_pct, money_pct}
    per_game = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    latest_ts = defaultdict(str)
    for row in rows:
        gid = row.get('game_id')
        mkt = str(row.get('market') or '').lower()
        side = str(row.get('side') or '').upper()
        metric = row.get('metric')
        val = row.get('value')
        ts = row.get('snapshot_ts') or ''
        if not (gid and mkt and side and metric and val is not None): continue
        per_game[gid][mkt][side][metric] = float(val)
        if ts > latest_ts[gid]: latest_ts[gid] = ts

    out = {}
    for gid, markets in per_game.items():
        snapshot = {}
        for mkt, sides in markets.items():
            if mkt not in ('ml', 'rl', 'total'): continue
            # Two sides per market. Pick = side with more money_pct.
            picks = []
            for side, metrics in sides.items():
                money = metrics.get('money_pct')
                bets = metrics.get('bets_pct')
                if money is not None:
                    picks.append((side, money, bets))
            if not picks: continue
            picks.sort(key=lambda x: -x[1])
            top_side, top_money, top_bets = picks[0]
            # Fade classification: if bets% > money% by 10+, public loves it
            # but money isn't following → fade the public → boost sharp side.
            fade_note = 'neutral'
            if top_bets is not None and top_money is not None:
                if top_bets - top_money >= 10:
                    fade_note = 'fade'  # public heavy but money quiet
                elif top_money - top_bets >= 10:
                    fade_note = 'boost'  # money heavier than bets = sharp
            div_val = None
            if top_bets is not None:
                div_val = int(round(top_money - top_bets))
            snapshot[mkt] = {
                'pick': top_side,
                'money': int(round(top_money)),
                'bets': int(round(top_bets or 0)),
                'div': div_val if div_val is not None else 0,
                'fade': fade_note,
                'source': 'so',
            }
        if snapshot:
            snapshot['pulled_at'] = latest_ts.get(gid)
            out[gid] = snapshot
    return out


def compute_lens_ml_from_context(c: dict, lens_fields: dict) -> tuple:
    """Compute lens majority for ML from context row.

    lens_fields shape: {'panel': 'panel_implied_margin',
                        'jerry': 'jerry_pred_spread',
                        'v3': 'projected_spread',
                        'v4': 'model_pred_spread',
                        'mc_json_col': 'mc_probabilities',
                        'mc_margin_key': 'mc_expected_margin',
                        'conf_col': 'signal_confluence_net'}
    All keys optional — missing ones skipped.
    """
    sides = {}
    for lens_name in ('panel', 'jerry', 'v3', 'v4'):
        col = lens_fields.get(lens_name)
        if col and col in c:
            sides[lens_name] = _side_from_margin(_f(c.get(col)))

    mc_col = lens_fields.get('mc_json_col')
    mc_key = lens_fields.get('mc_margin_key')
    if mc_col and mc_key:
        mc = c.get(mc_col) or {}
        if isinstance(mc, str):
            try: mc = json.loads(mc)
            except: mc = {}
        sides['mc'] = _side_from_margin(_f(mc.get(mc_key)))

    conf_col = lens_fields.get('conf_col')
    if conf_col and c.get(conf_col) is not None:
        n = c[conf_col] or 0
        if n > 0: sides['conf'] = 'H'
        elif n < 0: sides['conf'] = 'A'

    valid = [v for v in sides.values() if v]
    if not valid:
        return None, 0, 0
    votes = Counter(valid)
    lead, n = votes.most_common(1)[0]
    return lead, n, len(valid)


def compute_lens_total_from_context(c: dict, lens_fields: dict) -> tuple:
    close_tot = _f(c.get('close_total'))
    if close_tot is None:
        return None, 0, 0
    preds = []
    for tot_col in ('panel_total_col', 'jerry_total_col', 'v3_total_col', 'v4_total_col'):
        col = lens_fields.get(tot_col)
        if col and c.get(col) is not None:
            preds.append(_f(c.get(col)))
    mc_col = lens_fields.get('mc_json_col')
    mc_key = lens_fields.get('mc_total_key')
    if mc_col and mc_key:
        mc = c.get(mc_col) or {}
        if isinstance(mc, str):
            try: mc = json.loads(mc)
            except: mc = {}
        preds.append(_f(mc.get(mc_key)))
    dirs = ['O' if p > close_tot else ('U' if p < close_tot else None)
            for p in preds if p is not None]
    dirs = [d for d in dirs if d]
    if not dirs:
        return None, 0, 0
    votes = Counter(dirs)
    lead, n = votes.most_common(1)[0]
    return lead, n, len(dirs)


def build_alignment(c: dict, ext_rows: list, lens_fields: dict,
                     so_snapshot: dict | None = None) -> tuple[dict, dict]:
    oc_by_surface = {}
    ext_by_surface = defaultdict(list)
    latest_oc_pulled = None
    for e in ext_rows:
        surf = e.get('surface')
        if e.get('source') == 'oddscrowd':
            parsed = _parse_oc(e)
            if parsed and surf in ('ml', 'rl', 'total'):
                oc_by_surface[surf] = parsed
                if e.get('pulled_at') and (not latest_oc_pulled or e['pulled_at'] > latest_oc_pulled):
                    latest_oc_pulled = e['pulled_at']
        else:
            if surf in ('ml', 'rl', 'total'):
                ext_by_surface[surf].append(e)

    # 2026-08-26: fall back to ScoresAndOdds public splits for sports
    # OddsCrowd doesn't cover (NCAAF). Preserves same snapshot shape so
    # every downstream consumer (ensemble OC-flip gate, GameDetailV2
    # money-flow bars, etc.) sees the same data structure.
    if so_snapshot:
        for surf, snap in so_snapshot.items():
            if surf in ('ml', 'rl', 'total') and surf not in oc_by_surface:
                oc_by_surface[surf] = snap
                pulled = so_snapshot.get('pulled_at')
                if pulled and (not latest_oc_pulled or pulled > latest_oc_pulled):
                    latest_oc_pulled = pulled

    def _compute_rlm(surface: str, oc_side: str | None) -> dict:
        """Reverse line movement detector (2026-08-06).

        RLM = line moved OPPOSITE to public/money side. Historically one
        of the strongest sharp-money signals: sharps bet the unpopular
        side heavily enough to move the number against the public.

        Returns {rlm: bool, sharp_side: str | None, direction_note: str}.
        """
        if oc_side is None:
            return {'rlm': False, 'sharp_side': None, 'direction_note': 'no_money_data'}
        if surface == 'total':
            opn = c.get('open_total'); cur = c.get('current_total')
            if opn is None or cur is None:
                return {'rlm': False, 'sharp_side': None, 'direction_note': 'no_line_data'}
            try:
                opn, cur = float(opn), float(cur)
            except (TypeError, ValueError):
                return {'rlm': False, 'sharp_side': None, 'direction_note': 'bad_line_data'}
            if abs(cur - opn) < 0.25:
                return {'rlm': False, 'sharp_side': None, 'direction_note': 'no_movement'}
            line_side = 'O' if cur > opn else 'U'
            note = f'total {opn}→{cur}'
        elif surface == 'ml':
            opn = c.get('home_ml_open'); cur = c.get('home_ml_close') or c.get('home_ml_odds')
            if opn is None or cur is None:
                return {'rlm': False, 'sharp_side': None, 'direction_note': 'no_line_data'}
            try:
                opn, cur = int(opn), int(cur)
            except (TypeError, ValueError):
                return {'rlm': False, 'sharp_side': None, 'direction_note': 'bad_line_data'}
            if abs(cur - opn) < 10:  # thin ML drift is noise
                return {'rlm': False, 'sharp_side': None, 'direction_note': 'no_movement'}
            # more-negative home_ml = HOME more heavily favored → line moved toward HOME
            line_side = 'H' if cur < opn else 'A'
            note = f'home_ml {opn:+d}→{cur:+d}'
        else:
            return {'rlm': False, 'sharp_side': None, 'direction_note': 'unsupported_surface'}

        # RLM fires when line moves opposite to public money
        rlm = (line_side != oc_side)
        return {
            'rlm': rlm,
            'sharp_side': line_side if rlm else None,
            'direction_note': f'{note} · line→{line_side}, money→{oc_side}',
        }

    def market_status(surface: str) -> dict:
        oc = oc_by_surface.get(surface)
        ext = ext_by_surface.get(surface, [])
        if surface == 'total':
            over_ct = sum(1 for e in ext if e.get('pick_side') == 'OVER')
            under_ct = sum(1 for e in ext if e.get('pick_side') == 'UNDER')
            ext_lead = 'O' if over_ct > under_ct else ('U' if under_ct > over_ct else None)
            ext_count = max(over_ct, under_ct)
            ext_total = over_ct + under_ct
        else:
            home_ct = sum(1 for e in ext if e.get('pick_side') == 'HOME')
            away_ct = sum(1 for e in ext if e.get('pick_side') == 'AWAY')
            ext_lead = 'H' if home_ct > away_ct else ('A' if away_ct > home_ct else None)
            ext_count = max(home_ct, away_ct)
            ext_total = home_ct + away_ct
        oc_side = None
        if oc:
            if surface == 'total':
                oc_side = 'O' if oc['pick'] == 'OVER' else 'U'
            else:
                oc_side = 'H' if oc['pick'] == 'HOME' else 'A'
        aligned = None
        if ext_lead and oc_side:
            aligned = (ext_lead == oc_side)
        lens_lead, lens_ct, lens_total = (None, 0, 0)
        if surface == 'ml':
            lens_lead, lens_ct, lens_total = compute_lens_ml_from_context(c, lens_fields)
        elif surface == 'total':
            lens_lead, lens_ct, lens_total = compute_lens_total_from_context(c, lens_fields)
        rlm_info = _compute_rlm(surface, oc_side)
        return {
            'ext_lead': ext_lead, 'ext_count': ext_count, 'ext_total': ext_total,
            'money_side': oc_side,
            'money_pct': oc['money'] if oc else None,
            'bets_pct': oc['bets'] if oc else None,
            'div': oc['div'] if oc else None,
            'lens_side': lens_lead, 'lens_count': lens_ct, 'lens_total': lens_total,
            'aligned': aligned,
            'verdict': _verdict(aligned, ext_count, oc.get('div') if oc else None),
            # 2026-08-06: reverse line movement (line moved opposite to public $)
            'rlm': rlm_info['rlm'],
            'sharp_side': rlm_info['sharp_side'],
            'rlm_note': rlm_info['direction_note'],
        }

    ml_s = market_status('ml')
    rl_s = market_status('rl')
    tot_s = market_status('total')

    aligned_markets = sum(1 for m in (ml_s, rl_s, tot_s) if m['aligned'] is True)
    disagree_markets = sum(1 for m in (ml_s, rl_s, tot_s) if m['aligned'] is False)

    if aligned_markets >= 2:
        overall_verdict = 'aligned_strong' if aligned_markets == 3 else 'aligned'
        overall_aligned = True
    elif disagree_markets >= 2:
        overall_verdict = 'disagreement'
        overall_aligned = False
    elif aligned_markets == 1 and disagree_markets == 0:
        overall_verdict = 'aligned_soft'
        overall_aligned = True
    else:
        overall_verdict = 'no_data'
        overall_aligned = None

    align_status = {
        'ml': ml_s, 'rl': rl_s, 'total': tot_s,
        'overall': {
            'aligned': overall_aligned,
            'verdict': overall_verdict,
            'aligned_markets': aligned_markets,
        },
        'computed_at': datetime.now(timezone.utc).isoformat(),
    }
    oc_snapshot = {**oc_by_surface}
    if latest_oc_pulled:
        oc_snapshot['pulled_at'] = latest_oc_pulled

    return align_status, oc_snapshot


def update_context(sb_url: str, sb_key: str, context_table: str,
                   game_id: str, align_status: dict, oc_snapshot: dict) -> bool:
    payload = {
        'align_status': align_status,
        'oddscrowd_snapshot': oc_snapshot if oc_snapshot else None,
    }
    h = {'apikey': sb_key, 'Authorization': f'Bearer {sb_key}',
         'Content-Type': 'application/json', 'Prefer': 'return=minimal'}
    r = requests.patch(
        f'{sb_url}/rest/v1/{context_table}?game_id=eq.{game_id}',
        headers=h, json=payload, timeout=15,
    )
    if r.status_code not in (200, 204):
        print(f'    ⚠ patch {r.status_code}: {r.text[:200]}')
        return False
    return True


def compute_and_write(sb_url: str, sb_key: str, sport_code: str,
                      context_table: str, game_date: str,
                      lens_fields: dict, extra_select: str = '',
                      dry_run: bool = False) -> int:
    """One-shot entry point. Returns count updated."""
    ctxs = load_contexts(sb_url, sb_key, context_table, game_date, extra_select=extra_select)
    exts = load_externals(sb_url, sb_key, sport_code, game_date)
    # 2026-08-26: also try ScoresAndOdds public splits (fallback for
    # sports OC doesn't cover — NCAAF). No-op for sports where S&O has
    # no rows.
    so_snaps = load_public_splits_snapshot(sb_url, sb_key, sport_code, game_date)
    ext_total = sum(len(v) for v in exts.values())
    print(f'  {len(ctxs)} games · {ext_total} external rows · '
          f'{len(so_snaps)} games with S&O snapshots')
    updated = 0
    for c in ctxs:
        gid = c['game_id']
        ext_rows = exts.get(gid, [])
        align, oc_snap = build_alignment(c, ext_rows, lens_fields,
                                          so_snapshot=so_snaps.get(gid))
        ov = align['overall']
        print(f"  {c['away_team'][:14]:14s} @ {c['home_team'][:14]:14s}  "
              f"overall={ov['verdict']:16s} markets={ov['aligned_markets']}  "
              f"ml={align['ml']['verdict']} tot={align['total']['verdict']}")
        if not dry_run:
            if update_context(sb_url, sb_key, context_table, gid, align, oc_snap):
                updated += 1
    return updated
