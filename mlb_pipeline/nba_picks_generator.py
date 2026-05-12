"""
NBA conviction-tier pick generator (Phase 2).

For each NBA game on today's slate, applies the chalk-trap detector
(model_edge = nrGap + market_spread) and pace-adjusted total projection,
then writes one row per pick to nba_game_picks. Tier (PRIME/STRONG/LEAN)
gates app surfacing; resolver fills result post-game.

Mirrors app/index.tsx calcGameSweatScore NBA branch (line ~3500) so the
server is the single source of truth — app can keep local computation
for now, but cohort calibration reads from this table.

Why server-side: enables audit cohort calibration in mlb_tier_calibration,
historical pick logging without app launches, and consistent picks across
device/web surfaces.

Tier thresholds (informed-guess pre-audit; revisit Nov+ once n≥30):
  Spread/ATS edge:   LEAN ≥2pt   STRONG ≥4pt   PRIME ≥6pt
  Total edge (pace): LEAN ≥5pt   STRONG ≥8pt   PRIME ≥10pt

Run daily after nba_pipeline.py refreshes nba_team_stats.
"""
import os
import sys
import json
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
ODDS_API_KEY = os.environ.get('ODDS_API_KEY')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
WRITE_HEADERS = {**HEADERS, 'Content-Type': 'application/json',
                 'Prefer': 'resolution=merge-duplicates,return=minimal'}

SPREAD_LEAN = 2.0
SPREAD_STRONG = 4.0
SPREAD_PRIME = 6.0
# Total thresholds tightened 2026-05-08 after first shakedown — original
# (5/8/10) fired PRIME on 4/4 games of a 4-game slate. Pace formula matches
# textbook but BDL pace is season-averaged and doesn't reflect playoff
# slowdown, plus markets price NBA under-bias. Raised + paired with playoff
# pace dampener below. Audit cohort recalibrates Nov+.
TOTAL_LEAN = 7.0
TOTAL_STRONG = 11.0
TOTAL_PRIME = 15.0

# Playoff pace dampener — playoff games run ~3-4% slower than regular
# season pace (slower halfcourt sets, fewer transition possessions).
# BDL returns season-averaged pace, so without this our totals
# systematically overshoot during playoffs.
PLAYOFF_PACE_FACTOR = 0.96


def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')


def is_playoff_time():
    """Mirrors nba_pipeline.is_playoff_time — playoffs start April 19 2026."""
    return datetime.now() >= datetime(2026, 4, 19)


def _f(v):
    try: return float(v)
    except: return None


def fetch_team_stats():
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/nba_team_stats?select=*',
        headers=HEADERS, timeout=15
    )
    if r.status_code != 200:
        return {}
    return {row.get('team'): row for row in r.json()}


def fetch_odds_games():
    if not ODDS_API_KEY:
        print('No ODDS_API_KEY')
        return []
    r = requests.get(
        'https://api.the-odds-api.com/v4/sports/basketball_nba/odds',
        params={'apiKey': ODDS_API_KEY, 'regions': 'us',
                'markets': 'h2h,spreads,totals', 'oddsFormat': 'american'},
        timeout=20
    )
    if r.status_code != 200:
        print(f'Odds API error: {r.status_code}')
        return []
    return r.json()


def median(arr):
    return sorted(arr)[len(arr)//2] if arr else None


def extract_market(game):
    spreads, totals, hmls, amls = [], [], [], []
    for bm in game.get('bookmakers', []):
        for mkt in bm.get('markets', []):
            if mkt['key'] == 'spreads':
                home = next((o for o in mkt['outcomes'] if o['name'] == game['home_team']), None)
                if home and home.get('point') is not None:
                    spreads.append(home['point'])
            elif mkt['key'] == 'totals':
                t = mkt['outcomes'][0] if mkt.get('outcomes') else None
                if t and t.get('point') is not None:
                    totals.append(t['point'])
            elif mkt['key'] == 'h2h':
                for o in mkt['outcomes']:
                    if o['name'] == game['home_team']:
                        hmls.append(o['price'])
                    elif o['name'] == game['away_team']:
                        amls.append(o['price'])
    return median(spreads), median(totals), median(hmls), median(amls)


def tier_from_spread_edge(edge_abs):
    if edge_abs >= SPREAD_PRIME: return 'PRIME'
    if edge_abs >= SPREAD_STRONG: return 'STRONG'
    if edge_abs >= SPREAD_LEAN: return 'LEAN'
    return None


def tier_from_total_edge(edge_abs):
    if edge_abs >= TOTAL_PRIME: return 'PRIME'
    if edge_abs >= TOTAL_STRONG: return 'STRONG'
    if edge_abs >= TOTAL_LEAN: return 'LEAN'
    return None


def conviction_from_spread_edge(edge_abs, drift_aligned):
    base = 50 + min(40, edge_abs * 6)
    if drift_aligned: base += 5
    return min(100, int(round(base)))


def conviction_from_total_edge(edge_abs):
    base = 50 + min(40, edge_abs * 4)
    return min(100, int(round(base)))


def write_pick(record):
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/nba_game_picks?on_conflict=game_id,pick_type,pick_side',
        headers=WRITE_HEADERS, json=record, timeout=15
    )
    return r.status_code in (200, 201, 204), r.status_code, r.text[:200]


def has_star_out(injury_note):
    return bool(injury_note) and 'OUT' in injury_note.upper()


def generate_picks_for_game(game, team_stats):
    """Return list of pick records for one game (may be 0-3 rows)."""
    home_team = game['home_team']
    away_team = game['away_team']
    game_id = game['id']

    h = team_stats.get(home_team) or next(
        (v for k, v in team_stats.items() if home_team.endswith(k.split(' ')[-1])), None)
    a = team_stats.get(away_team) or next(
        (v for k, v in team_stats.items() if away_team.endswith(k.split(' ')[-1])), None)
    if not h or not a:
        print(f'  ⚠️  {away_team} @ {home_team} — missing team stats')
        return []

    h_nr = _f(h.get('net_rating'))
    a_nr = _f(a.get('net_rating'))
    if h_nr is None or a_nr is None:
        return []

    spread, total, hml, aml = extract_market(game)

    # Recency drift
    h_l10 = _f(h.get('last_10_net_rating'))
    a_l10 = _f(a.get('last_10_net_rating'))
    home_drift = (h_l10 - h_nr) if h_l10 is not None else 0.0
    away_drift = (a_l10 - a_nr) if a_l10 is not None else 0.0

    nr_gap = h_nr - a_nr
    pace_avg = None
    if h.get('pace') is not None and a.get('pace') is not None:
        pace_avg = round((_f(h['pace']) + _f(a['pace'])) / 2, 1)

    base = {
        'game_id': game_id,
        'game_date': today_et(),
        'season': '2025-26',
        'home_team': home_team,
        'away_team': away_team,
        'net_rating_gap': round(nr_gap, 2),
        'market_spread': spread,
        'pace_avg': pace_avg,
        'market_total': total,
        'home_ml': hml,
        'away_ml': aml,
        'home_drift': round(home_drift, 2),
        'away_drift': round(away_drift, 2),
        'home_injury_note': h.get('injury_note'),
        'away_injury_note': a.get('injury_note'),
    }

    picks = []

    # ── Star OUT skip ────────────────────────────────────────
    home_out = has_star_out(h.get('injury_note'))
    away_out = has_star_out(a.get('injury_note'))
    if home_out or away_out:
        out_team = home_team if home_out else away_team
        out_note = (h if home_out else a).get('injury_note', '')
        picks.append({
            **base,
            'pick_type': 'star_out_skip',
            'pick_side': 'skip',  # sentinel — NULL would defeat unique dedupe on re-run
            'pick_label': f'Star OUT — skip lean ({out_team})',
            'conviction': None,
            'tier': None,
            'signals': {'reason': 'star_out', 'note': out_note},
        })
        return picks  # no leans fire when star is out

    # ── ATS / spread edge ────────────────────────────────────
    if spread is not None:
        model_edge = nr_gap + spread  # home perspective
        edge_abs = abs(model_edge)
        tier = tier_from_spread_edge(edge_abs)
        if tier:
            value_is_home = model_edge > 0
            value_team = home_team if value_is_home else away_team
            value_is_dog = (value_is_home and spread > 0) or (not value_is_home and spread < 0)
            label_kind = 'dog cover' if value_is_dog else 'ATS edge'
            net_drift = home_drift - away_drift
            drift_aligned = (
                (value_is_home and net_drift >= 2) or
                (not value_is_home and net_drift <= -2)
            )
            label = f'{value_team} {label_kind}'
            if drift_aligned:
                label += ' • L10 trending up'
            picks.append({
                **base,
                'pick_type': 'ats',
                'pick_side': 'home' if value_is_home else 'away',
                'pick_label': label,
                'conviction': conviction_from_spread_edge(edge_abs, drift_aligned),
                'tier': tier,
                'model_edge': round(model_edge, 2),
                'signals': {
                    'nr_gap': round(nr_gap, 2),
                    'market_spread': spread,
                    'model_edge': round(model_edge, 2),
                    'drift_aligned': drift_aligned,
                    'home_drift': round(home_drift, 2),
                    'away_drift': round(away_drift, 2),
                },
            })

    # ── Pace-adjusted total ─────────────────────────────────
    if total is not None and h.get('pace') is not None and a.get('pace') is not None:
        h_off = _f(h.get('offensive_rating')) or 112
        a_off = _f(a.get('offensive_rating')) or 112
        h_def = _f(h.get('defensive_rating')) or 112
        a_def = _f(a.get('defensive_rating')) or 112
        avg_pace = (_f(h['pace']) + _f(a['pace'])) / 2
        if is_playoff_time():
            avg_pace *= PLAYOFF_PACE_FACTOR
        home_expected = ((h_off + a_def) / 2) / 100 * avg_pace
        away_expected = ((a_off + h_def) / 2) / 100 * avg_pace
        projected_total = round(home_expected + away_expected, 1)
        total_edge = projected_total - total
        edge_abs = abs(total_edge)
        tier = tier_from_total_edge(edge_abs)
        if tier:
            side = 'over' if total_edge > 0 else 'under'
            label = f'{side.upper()} {total} lean'
            picks.append({
                **base,
                'pick_type': 'total',
                'pick_side': side,
                'pick_label': label,
                'conviction': conviction_from_total_edge(edge_abs),
                'tier': tier,
                'projected_total': projected_total,
                'total_edge': round(total_edge, 2),
                'signals': {
                    'projected_total': projected_total,
                    'market_total': total,
                    'total_edge': round(total_edge, 2),
                    'pace_avg': round(avg_pace, 1),
                },
            })

    return picks


def run():
    print(f'NBA picks generator — {today_et()}')
    games = fetch_odds_games()
    if not games:
        print('No NBA games on slate')
        return
    team_stats = fetch_team_stats()
    print(f'Pulled {len(games)} games, {len(team_stats)} team rows')

    success = 0
    skipped_started = 0
    total_picks = 0
    by_tier = {'PRIME': 0, 'STRONG': 0, 'LEAN': 0, 'SKIP': 0}

    for g in games:
        try:
            commence = g.get('commence_time')
            if commence:
                dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) >= dt:
                    skipped_started += 1
                    continue
            picks = generate_picks_for_game(g, team_stats)
            if not picks:
                print(f'  ─ {g["away_team"]} @ {g["home_team"]} — no edge ≥ threshold')
                continue
            for p in picks:
                # serialize JSONB
                if isinstance(p.get('signals'), dict):
                    p['signals'] = p['signals']  # supabase REST accepts dict for jsonb
                ok, code, msg = write_pick(p)
                if ok:
                    success += 1
                    total_picks += 1
                    tag = p['tier'] or 'SKIP'
                    by_tier[tag] = by_tier.get(tag, 0) + 1
                    print(f'  ✅ {g["away_team"]} @ {g["home_team"]} — [{tag}] {p["pick_label"]} (conv {p.get("conviction")})')
                else:
                    print(f'  ❌ {g["away_team"]} @ {g["home_team"]} — {code} {msg}')
        except Exception as e:
            print(f'  ⚠️  {g.get("home_team", "?")}: {e}')

    print(f'\nDone. {success} picks written, {skipped_started} games skipped (in-progress)')
    print(f'  by tier: PRIME={by_tier["PRIME"]} STRONG={by_tier["STRONG"]} LEAN={by_tier["LEAN"]} SKIP={by_tier["SKIP"]}')


if __name__ == '__main__':
    run()
