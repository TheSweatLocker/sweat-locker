"""Pre-publish slate review — dumps FULL signal stack per game so a
human (or an AI) can sanity-check picks against every dimension the
ensemble weighs, not just the top-line tier.

Motivated by 2026-08-31 audit finding: independent analysis using
only MC + panel + xERA cherry-picked 3 wrong sides. The ensemble
knew better because it weighed 4 external handicappers + cohort +
H2H + sharp scenarios that got skipped in the shallow review.

Prints, per game:
  - Market lines + starters + wRC+ + BP + park + weather
  - All 4 model projections (Panel · V4 · MC · Jerry) with divergence flags
  - Ensemble top pick + alt-market picks (ML/RL/Total) with conviction
  - Top-8 ensemble supporting contributions (signal_class, weight, n, prose)
  - Opposing "losing_market_notes" (signals that fired on the other side)
  - Cohort v2 breakdown (raw signal net + tier)
  - Sharp scenarios matched (with hit_rate + n)
  - Public split flags — but only surfaces TRIPLE_CONFIRMED loud
    (LEAN + CONFIRMED tiers currently inverted, quarantined 2026-08-31)
  - External handicapper picks with per-source W-L records from
    external_source_track_record (lifetime)
  - Value math per market: MC implied vs market implied → +/- pp edge

Usage:
    python review_slate.py                    # today, all games
    python review_slate.py --date 2026-09-01
    python review_slate.py --game <game_id>   # single game
    python review_slate.py --sport NCAAF      # cross-sport (defaults MLB)
"""
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

SPORT_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
}


def _american_to_implied(odds):
    if odds is None: return None
    try:
        o = float(odds)
        return abs(o) / (abs(o) + 100) if o < 0 else 100 / (o + 100)
    except (TypeError, ValueError):
        return None


def _fmt_pct(p, width=5):
    return f'{p*100:>{width-1}.1f}%' if p is not None else '  —  '


def _fetch_ctx(sport: str, date: str, single_gid: str | None) -> list:
    table = SPORT_TABLE.get(sport)
    if not table: return []
    if single_gid:
        r = requests.get(f'{SB}/rest/v1/{table}?game_id=eq.{single_gid}', headers=H, timeout=15)
    else:
        r = requests.get(f'{SB}/rest/v1/{table}?game_date=eq.{date}&order=game_id', headers=H, timeout=15)
    if r.status_code != 200:
        print(f'  ⚠ fetch failed {r.status_code}: {r.text[:150]}')
        return []
    data = r.json()
    return data if isinstance(data, list) else []


def _fetch_externals_by_game(date: str, sport: str) -> dict:
    r = requests.get(f'{SB}/rest/v1/external_picks?game_date=eq.{date}&sport=eq.{sport}&select=game_id,source,surface,pick_side,pick_line,odds_american,confidence,raw_text', headers=H, timeout=20)
    if r.status_code != 200: return {}
    by = {}
    for e in (r.json() or []):
        by.setdefault(e['game_id'], []).append(e)
    return by


def _fetch_source_records(sport: str) -> dict:
    """Load per-source lifetime W-L for the sport (from external_source_track_record).
    Returns {(source, surface): {n_wins, n_losses, hit_rate, roi}}."""
    r = requests.get(f'{SB}/rest/v1/external_source_track_record?sport=eq.{sport}&window_days=eq.9999&select=source,surface,n_wins,n_losses,hit_rate,roi', headers=H, timeout=15)
    if r.status_code != 200: return {}
    out = {}
    for row in (r.json() or []):
        out[(row['source'], row.get('surface') or 'ALL')] = row
    return out


def _fetch_split_flags_by_game(date: str, sport: str) -> dict:
    r = requests.get(f'{SB}/rest/v1/line_movement_flags?first_seen_at=gte.{date}T00:00:00&first_seen_at=lt.{date}T23:59:59&sport=eq.{sport}&select=game_id,market,side,classification,money_pct,bets_pct', headers=H, timeout=20)
    if r.status_code != 200: return {}
    by = {}
    for f in (r.json() or []):
        by.setdefault(f['game_id'], []).append(f)
    return by


def _fetch_scenarios_by_game(date: str) -> dict:
    try:
        r = requests.get(f'{SB}/rest/v1/sharp_scenario_game_matches?game_date=eq.{date}&select=game_id,scenario_key,market,side,hit_rate,sample_n', headers=H, timeout=15)
        if r.status_code != 200: return {}
        by = {}
        for s in (r.json() or []):
            by.setdefault(s['game_id'], []).append(s)
        return by
    except Exception:
        return {}


def _model_divergence_flag(vals: list[float | None], min_gap: float = 1.5) -> str:
    """Return a warning string when models disagree by min_gap+ runs."""
    nums = [v for v in vals if v is not None]
    if len(nums) < 2: return ''
    gap = max(nums) - min(nums)
    if gap >= min_gap:
        return f'  ⚠ model gap {gap:.2f} runs — 🚨 divergence'
    return ''


def _print_game(g: dict, ext_by: dict, split_by: dict, scen_by: dict,
                src_records: dict, sport: str):
    if isinstance(g, str): return
    gid = g.get('game_id', '?')
    home, away = g.get('home_team', '?'), g.get('away_team', '?')
    pp = g.get('primary_play') or {}
    mc = g.get('mc_probabilities') or {}

    print('\n' + '═' * 78)
    print(f'  {away} @ {home}')
    print('═' * 78)

    # Market lines
    sp = g.get('close_spread')
    tot = g.get('close_total')
    hml = g.get('home_ml_close')
    aml = g.get('away_ml_close')
    print(f'  MARKET   sp={sp} · tot={tot} · ML {aml}/{hml}')

    # Starters
    if sport == 'MLB':
        print(f'  SP       A:{(g.get("away_pitcher") or "?")[:16]:16} xE {g.get("away_sp_xera","—")} · '
              f'H:{(g.get("home_pitcher") or "?")[:16]:16} xE {g.get("home_sp_xera","—")}')
        print(f'  OFF      A wRC+ {g.get("away_wrc_plus","—")} L5{g.get("away_last5_run_diff","—"):+} · '
              f'H wRC+ {g.get("home_wrc_plus","—")} L5{g.get("home_last5_run_diff","—"):+}')
        print(f'  BP       A ERA {g.get("away_bullpen_era","—")} · H ERA {g.get("home_bullpen_era","—")}')
    print(f'  PARK     PF={g.get("park_run_factor")} · wx {g.get("temperature","—")}F wind {g.get("wind_speed","—")}mph')

    # Models — total + margin
    panel_t = g.get('panel_implied_total')
    panel_m = g.get('panel_implied_margin')
    v4_t = g.get('model_pred_total')
    v4_sp = g.get('model_pred_spread')
    mc_t = mc.get('mc_mean_total')
    print(f'  MODELS   panel_tot={panel_t} panel_mgn={panel_m}  ·  V4 tot={v4_t} sp={v4_sp}  ·  MC mean={mc_t}')
    diverg = _model_divergence_flag([panel_t, v4_t, mc_t])
    if diverg: print(f'  {diverg}')

    # MC + value math
    if mc:
        mc_home = mc.get('mc_p_home_win')
        mc_away = mc.get('mc_p_away_win')
        mc_over = mc.get('mc_p_over')
        mc_under = mc.get('mc_p_under')
        home_imp = _american_to_implied(hml)
        away_imp = _american_to_implied(aml)
        mc_home_edge = (mc_home - home_imp) * 100 if (mc_home is not None and home_imp is not None) else None
        mc_away_edge = (mc_away - away_imp) * 100 if (mc_away is not None and away_imp is not None) else None
        print(f'  MC       home_w={_fmt_pct(mc_home)} away_w={_fmt_pct(mc_away)}  ·  over={_fmt_pct(mc_over)} under={_fmt_pct(mc_under)}')
        val_notes = []
        if mc_home_edge is not None:
            val_notes.append(f'HOME ML {mc_home_edge:+.1f}pp value')
        if mc_away_edge is not None:
            val_notes.append(f'AWAY ML {mc_away_edge:+.1f}pp value')
        if val_notes:
            print(f'  VALUE    {" · ".join(val_notes)}')

    # Ensemble top + alts
    print(f'  ENSEMBLE TOP: {pp.get("tier","—"):<9} {pp.get("label","—")}  conv={pp.get("conviction","—")}  score={pp.get("score","—")}  stake={pp.get("recommended_stake","—")}')
    am = pp.get('_ensemble_all_markets') or {}
    for mkt in ('ml', 'rl', 'total'):
        m = am.get(mkt) or {}
        if m.get('pick'):
            print(f'           alt {mkt.upper():5}: {m.get("tier","—"):<9} {m.get("label","—"):30} conv={m.get("conviction",0)}')

    # Signal contributions
    srcs = pp.get('_ensemble_sources') or []
    if srcs:
        print(f'  SIGNALS SUPPORTING PICK ({len(srcs)}):')
        for s in srcs[:10]:
            cls = (s.get('class') or '?')[:14]
            n = s.get('n', 0)
            hr = s.get('hit_rate')
            hr_s = f'hr={hr:.2f}' if isinstance(hr, (int, float)) else 'hr=—'
            print(f'    [{cls:14}] w={s.get("weight",0):.2f} n={n:>4} {hr_s} · {(s.get("prose") or "")[:78]}')

    # Opposing signals
    lm = pp.get('_losing_market_notes') or []
    if lm:
        print(f'  OPPOSING SIGNALS ({len(lm)}):')
        for n in lm[:5]:
            print(f'    · {str(n)[:110]}')

    # Split flags — only TRIPLE-CONFIRMED is trusted post-audit
    fl = split_by.get(gid, [])
    if fl:
        loud = [f for f in fl if 'TRIPLE_CONFIRMED' in str(f.get('classification') or '')]
        muted = [f for f in fl if 'TRIPLE_CONFIRMED' not in str(f.get('classification') or '')]
        if loud:
            print(f'  🎯 TRUSTED SPLIT FLAGS (TRIPLE only):')
            for f in loud:
                print(f'    [{f.get("classification","?")}] {f.get("market","?"):5} {f.get("side","?"):5} · money/bets {f.get("money_pct","-")}/{f.get("bets_pct","-")}')
        if muted:
            print(f'  ⚠ Muted split flags ({len(muted)}) — LEAN/CONFIRMED quarantined pending inversion RCA')

    # Sharp scenarios
    sc = sorted(scen_by.get(gid, []), key=lambda x: -(x.get('hit_rate') or 0))
    hot_sc = [s for s in sc if (s.get('hit_rate') or 0) >= 60 and (s.get('sample_n') or 0) >= 20]
    if hot_sc:
        print(f'  SHARP SCENARIOS (hit≥60% n≥20 · showing {min(len(hot_sc),6)}):')
        for s in hot_sc[:6]:
            print(f'    {(s.get("scenario_key") or "?")[:38]:38} {s.get("market"):5} {s.get("side"):5} · {s.get("hit_rate")}% (n={s.get("sample_n")})')

    # Externals with per-source W-L
    ex = ext_by.get(gid, [])
    if ex:
        # Group by (surface, side) so we can compute consensus
        from collections import Counter
        by_market = {}
        for e in ex:
            by_market.setdefault(e.get('surface') or '?', []).append(e)
        for mkt, picks in by_market.items():
            sides = Counter((p.get('pick_side') or '?') for p in picks)
            majority = sides.most_common(1)[0] if sides else None
            if majority:
                consensus = f'{majority[0]}: {majority[1]}/{sum(sides.values())}'
            else:
                consensus = ''
            print(f'  EXTERNALS {mkt.upper()} ({sum(sides.values())} picks · consensus {consensus}):')
            for p in picks[:8]:
                rec = src_records.get((p['source'], mkt)) or src_records.get((p['source'], 'ALL')) or {}
                rec_str = f'{rec.get("n_wins",0)}-{rec.get("n_losses",0)} ({rec.get("hit_rate",0):.1f}%)' if rec else 'no record'
                print(f'    {p["source"]:14} {(p.get("pick_side") or "?"):5} @{p.get("odds_american","-")} · lifetime {rec_str}')


def run(date: str, sport: str, single_gid: str | None = None) -> None:
    if not date:
        date = (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()
    print(f'╔════════════════════════════════════════════════════════════════════════════╗')
    print(f'║  SLATE REVIEW · {sport} · {date}' + ' ' * (76 - 20 - len(sport) - len(date)) + '║')
    print(f'╠════════════════════════════════════════════════════════════════════════════╣')
    print(f'║  Every signal class the ensemble weighs. Use to sanity-check picks         ║')
    print(f'║  before publish — cross-reference top-line tier against opposing signals,  ║')
    print(f'║  model divergence, external consensus + per-source W-L.                    ║')
    print(f'╚════════════════════════════════════════════════════════════════════════════╝')

    ctxs = _fetch_ctx(sport, date, single_gid)
    if not ctxs:
        print(f'  no games loaded for {sport} · {date}')
        return
    print(f'  {len(ctxs)} game(s) loaded')

    ext_by = _fetch_externals_by_game(date, sport)
    split_by = _fetch_split_flags_by_game(date, sport)
    scen_by = _fetch_scenarios_by_game(date) if sport == 'MLB' else {}
    src_records = _fetch_source_records(sport)
    print(f'  externals={sum(len(v) for v in ext_by.values())} · split_flags={sum(len(v) for v in split_by.values())} · scenarios={sum(len(v) for v in scen_by.values())} · source_records={len(src_records)}')

    for g in ctxs:
        _print_game(g, ext_by, split_by, scen_by, src_records, sport)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    ap.add_argument('--sport', default='MLB', choices=list(SPORT_TABLE.keys()))
    ap.add_argument('--game', dest='single_gid', help='Single game_id (skips date filter)')
    args = ap.parse_args()
    run(date=args.date, sport=args.sport, single_gid=args.single_gid)


if __name__ == '__main__':
    main()
