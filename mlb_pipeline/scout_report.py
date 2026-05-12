"""Inning-bucket scout report for tonight's MLB slate.

Reads from mlb_game_context, mlb_pitcher_stats, mlb_team_offense, and mlb_bullpen_stats
to print a per-game side-by-side breakdown of how each team performs in
innings 1-3, 4-6, and 7-9 (offense + pitching + bullpen).

Designed for personal scouting — Hard Rock-style inning bucket bets.

Usage:
    python scout_report.py              # tonight's slate (ET)
    python scout_report.py 2026-04-28   # specific date
"""
import os
import sys
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def sb_get(table, params):
    """Query Supabase via PostgREST. params is a dict; values can be strings."""
    qs = urllib.parse.urlencode(params, safe=",.()")
    url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  Supabase error {e.code}: {e.read().decode()[:200]}")
        return []


def fmt(val, decimals=2, default="—"):
    if val is None:
        return default
    try:
        return f"{float(val):.{decimals}f}"
    except (TypeError, ValueError):
        return str(val)


def fmt_int(val, default="—"):
    if val is None:
        return default
    try:
        return str(int(val))
    except (TypeError, ValueError):
        return str(val)


def get_today_et():
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime("%Y-%m-%d")


def load_class_projections():
    """Load pitcher offense-class projections cache (built by
    compute_pitcher_class_projections.py). Keyed by pitcher name."""
    import json
    from pathlib import Path
    cache_path = Path(__file__).parent / 'data' / 'pitcher_class_projections.json'
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, 'r') as f:
            data = json.load(f)
        # Re-key by pitcher name (case-insensitive) for lookup by name
        return {v['name'].lower(): v for v in data.values() if v.get('name')}
    except Exception:
        return {}


def pitcher_prop_board(name, proj):
    """Return list of strings — lean analysis at common book lines for BB,
    Outs, and K props using L7 rolling averages. Skips openers (avg IP < 3).

    Common lines:
      BB: 1.5, 2.5
      Outs: 11.5, 14.5, 16.5, 17.5
      K: 4.5, 5.5, 6.5, 7.5, 8.5
    """
    if not proj or not proj.get('l7_rolling'):
        return []
    l7 = proj['l7_rolling']
    avg_ip = l7.get('avg_ip', 0)
    if avg_ip < 3.0:
        return [f"⚠️ Opener/relief profile (avg {avg_ip} IP) — skip starter props"]

    bb_avg = l7['avg_bb']; outs_avg = round(avg_ip * 3, 1); k_avg = l7['avg_k']
    BB_LINES = [1.5, 2.5]
    OUTS_LINES = [11.5, 14.5, 16.5, 17.5]
    K_LINES = [4.5, 5.5, 6.5, 7.5, 8.5]

    def leans_for(avg_val, lines, threshold):
        """Return list of strong leans (mean is ≥threshold above or below the line)."""
        out = []
        for line in lines:
            diff = avg_val - line
            if diff >= threshold:
                out.append(f"O{line} (+{diff:.1f})")
            elif diff <= -threshold:
                out.append(f"U{line} ({diff:.1f})")
        return out

    bb_leans = leans_for(bb_avg, BB_LINES, 0.5)
    outs_leans = leans_for(outs_avg, OUTS_LINES, 1.5)
    k_leans = leans_for(k_avg, K_LINES, 1.0)

    lines = [f"L7 ({l7['n_starts']} starts): {bb_avg} BB / {l7['avg_hits']} H / {k_avg} K / {avg_ip} IP per start ({l7.get('whip', '—')} WHIP)"]
    lines.append(f"BB: " + (", ".join(bb_leans) if bb_leans else "no clean lean"))
    lines.append(f"Outs: " + (", ".join(outs_leans) if outs_leans else "middle of lines"))
    lines.append(f"K: " + (", ".join(k_leans) if k_leans else "middle of lines"))
    return lines


def opp_wrc_to_bucket(wrc):
    """Match opponent wRC+ to projection class bucket label."""
    if wrc is None:
        return None
    try:
        w = float(wrc)
    except (TypeError, ValueError):
        return None
    if w <= 90: return 'le_90'
    if w <= 100: return '91_100'
    if w <= 110: return '101_110'
    if w <= 120: return '111_120'
    return 'ge_121'


def fetch_games(game_date):
    return sb_get(
        "mlb_game_context",
        {
            "game_date": f"eq.{game_date}",
            "select": "away_team,home_team,home_pitcher,away_pitcher,venue,close_spread,close_total,projected_spread,projected_total,signal_confluence_net,nrfi_score,home_bp_relievers_3d,away_bp_relievers_3d,home_lineup_ops,away_lineup_ops,home_wrc_plus,away_wrc_plus,lineup_confirmed,umpire",
        },
    )


_UMP_LOOKUP = None


def get_ump_stats(name):
    """Lazy-load mlb_umpires lookup; return stats for a name or None."""
    global _UMP_LOOKUP
    if _UMP_LOOKUP is None:
        rows = sb_get("mlb_umpires", {"select": "ump_name,k_rate_above_avg,over_rate,nrfi_rate,games_sampled"})
        _UMP_LOOKUP = {r["ump_name"].lower(): r for r in (rows or []) if r.get("ump_name")}
    return _UMP_LOOKUP.get((name or '').strip().lower())


def ump_signal_summary(ump):
    """Return concise audit-anchored signal flags for an umpire."""
    if not ump or ump.get('games_sampled', 0) < 30:
        return None
    over = ump.get('over_rate')
    k = ump.get('k_rate_above_avg')
    nrfi = ump.get('nrfi_rate')
    flags = []
    if over is not None:
        if over <= 0.45:
            flags.append(f"🚫 OVER fade (audit: 20% OVER w/ ≤0.45 ump, n=15)")
        elif over >= 0.55:
            flags.append(f"📈 OVER-friendly ({over:.2f}, audit 56%)")
    if k is not None:
        if k >= 0.2:
            flags.append(f"🔥 K-friendly ({k:+.1f}, K Overs 100% n=9)")
        elif k <= -0.2:
            flags.append(f"❄️ K-hostile ({k:+.1f}, K Overs 60% n=15)")
    if nrfi is not None:
        if nrfi >= 0.55:
            flags.append(f"🔒 NRFI-friendly ({nrfi:.2f}, audit 60.7% NRFI)")
        elif nrfi <= 0.45:
            flags.append(f"🌋 YRFI-friendly ({nrfi:.2f}, NRFI 40.8%)")
    return flags


def lineup_degradation_flag(lineup_ops, team_wrc):
    """Compare confirmed lineup OPS to season team wRC+ (proxy as wrc/0.720*100).
    Returns (delta_pts, flag_label) where flag fires when starting 9 is
    materially weaker than season-average team (key bats sitting).

    Why: pipeline currently bets like the regular lineup is in. When 2+
    regulars are scratched/rested, lineup quality drops 5-10 wRC+ pts and
    OVER probability shifts. Audit cohort can't backfill (lineup_ops is
    transient on game_context) but live signal is actionable today.
    """
    if lineup_ops is None or team_wrc is None:
        return None, None
    try:
        lineup_wrc_proxy = (float(lineup_ops) / 0.720) * 100
        team_wrc_f = float(team_wrc)
        delta = round(lineup_wrc_proxy - team_wrc_f, 1)
    except (TypeError, ValueError):
        return None, None
    if delta <= -10:
        return delta, "🚨 LINEUP DEGRADED 10+ pts (key bats sitting)"
    if delta <= -5:
        return delta, "⚠️ Lineup softer than season (regulars resting?)"
    if delta >= +10:
        return delta, "🔥 Stacked lineup (above season baseline)"
    return delta, None


def fetch_pitcher(name):
    if not name:
        return {}
    rows = sb_get(
        "mlb_pitcher_stats",
        {
            "player_name": f"eq.{name}",
            "select": "player_name,xera,k_pct,first_inning_era,first_inning_whip,innings_1_3_era,innings_1_3_whip,innings_1_3_k_pct,innings_1_3_bb_pct,innings_1_3_hr_per_9,innings_1_3_ip,innings_4_6_era,innings_4_6_whip,innings_4_6_k_pct,innings_4_6_bb_pct,innings_4_6_hr_per_9,innings_4_6_ip,innings_7_9_era,innings_7_9_whip,innings_7_9_k_pct,innings_7_9_bb_pct,innings_7_9_hr_per_9,innings_7_9_ip,last_3_era",
            "limit": "1",
        },
    )
    return rows[0] if rows else {}


def fetch_team_offense(team):
    if not team:
        return {}
    rows = sb_get(
        "mlb_team_offense",
        {
            "team": f"eq.{team}",
            "select": "team,wrc_plus,ops,k_pct,runs_per_game,last10_runs_per_game,last10_runs_allowed,last10_run_diff,last10_games_sampled,inning_1_runs_per_game,inning_1_ops,inning_1_wrc_plus,innings_1_3_runs_per_game,innings_1_3_ops,innings_1_3_wrc_plus,innings_1_3_k_pct,innings_1_3_bb_pct,innings_4_6_runs_per_game,innings_4_6_ops,innings_4_6_wrc_plus,innings_4_6_k_pct,innings_4_6_bb_pct,innings_7_9_runs_per_game,innings_7_9_ops,innings_7_9_wrc_plus,innings_7_9_k_pct,innings_7_9_bb_pct",
            "limit": "1",
        },
    )
    return rows[0] if rows else {}


def fetch_bullpen(team):
    if not team:
        return {}
    rows = sb_get(
        "mlb_bullpen_stats",
        {
            "team": f"eq.{team}",
            "select": "team,bullpen_era,save_pct,pitching_1_3_era,pitching_1_3_whip,pitching_1_3_k_pct,pitching_1_3_bb_pct,pitching_1_3_hr_per_9,pitching_4_6_era,pitching_4_6_whip,pitching_4_6_k_pct,pitching_4_6_bb_pct,pitching_4_6_hr_per_9,pitching_7_9_era,pitching_7_9_whip,pitching_7_9_k_pct,pitching_7_9_bb_pct,pitching_7_9_hr_per_9,pitching_7_9_ip",
            "limit": "1",
        },
    )
    return rows[0] if rows else {}


def gassed_flag(relievers_3d):
    if relievers_3d is None:
        return ""
    try:
        n = int(relievers_3d)
    except (TypeError, ValueError):
        return ""
    if n >= 12:
        return " 🚨 GASSED"
    if n >= 9:
        return " ⚠️ heavy use"
    return " ✅ rested"


def render_bucket(label, away_team, home_team, away_off, home_off, away_pen, home_pen,
                  away_sp_label, home_sp_label, away_sp, home_sp, bucket_key):
    print(f"\n  ── INNINGS {label} ──")

    # Pitching side
    away_pitch_era = away_sp.get(f"innings_{bucket_key}_era") if away_sp else None
    home_pitch_era = home_sp.get(f"innings_{bucket_key}_era") if home_sp else None
    away_pitch_whip = away_sp.get(f"innings_{bucket_key}_whip") if away_sp else None
    home_pitch_whip = home_sp.get(f"innings_{bucket_key}_whip") if home_sp else None
    away_pitch_kpct = away_sp.get(f"innings_{bucket_key}_k_pct") if away_sp else None
    home_pitch_kpct = home_sp.get(f"innings_{bucket_key}_k_pct") if home_sp else None
    away_pitch_ip = away_sp.get(f"innings_{bucket_key}_ip") if away_sp else None
    home_pitch_ip = home_sp.get(f"innings_{bucket_key}_ip") if home_sp else None

    away_pitch_bb = away_sp.get(f"innings_{bucket_key}_bb_pct") if away_sp else None
    home_pitch_bb = home_sp.get(f"innings_{bucket_key}_bb_pct") if home_sp else None
    away_pitch_hr9 = away_sp.get(f"innings_{bucket_key}_hr_per_9") if away_sp else None
    home_pitch_hr9 = home_sp.get(f"innings_{bucket_key}_hr_per_9") if home_sp else None

    print(f"    Starter pitching:")
    print(f"      {away_team} ({away_sp_label}): ERA {fmt(away_pitch_era)} / WHIP {fmt(away_pitch_whip)} / K% {fmt(away_pitch_kpct, 1)} / BB% {fmt(away_pitch_bb, 1)} / HR/9 {fmt(away_pitch_hr9)} ({fmt(away_pitch_ip, 1)} IP)")
    print(f"      {home_team} ({home_sp_label}): ERA {fmt(home_pitch_era)} / WHIP {fmt(home_pitch_whip)} / K% {fmt(home_pitch_kpct, 1)} / BB% {fmt(home_pitch_bb, 1)} / HR/9 {fmt(home_pitch_hr9)} ({fmt(home_pitch_ip, 1)} IP)")

    # Team-level pitching from bullpen_stats (whole staff blended for 1-3/4-6, ~bullpen for 7-9)
    away_team_era = away_pen.get(f"pitching_{bucket_key}_era") if away_pen else None
    home_team_era = home_pen.get(f"pitching_{bucket_key}_era") if home_pen else None
    pen_label = "Bullpen" if bucket_key == "7_9" else "Team pitching (blended)"
    print(f"    {pen_label} season-long:")
    print(f"      {away_team}: ERA {fmt(away_team_era)} / WHIP {fmt(away_pen.get(f'pitching_{bucket_key}_whip'))} / K% {fmt(away_pen.get(f'pitching_{bucket_key}_k_pct'), 1)} / BB% {fmt(away_pen.get(f'pitching_{bucket_key}_bb_pct'), 1)} / HR/9 {fmt(away_pen.get(f'pitching_{bucket_key}_hr_per_9'))}")
    print(f"      {home_team}: ERA {fmt(home_team_era)} / WHIP {fmt(home_pen.get(f'pitching_{bucket_key}_whip'))} / K% {fmt(home_pen.get(f'pitching_{bucket_key}_k_pct'), 1)} / BB% {fmt(home_pen.get(f'pitching_{bucket_key}_bb_pct'), 1)} / HR/9 {fmt(home_pen.get(f'pitching_{bucket_key}_hr_per_9'))}")

    # Offense
    away_off_rpg = away_off.get(f"innings_{bucket_key}_runs_per_game") if away_off else None
    home_off_rpg = home_off.get(f"innings_{bucket_key}_runs_per_game") if home_off else None
    away_off_ops = away_off.get(f"innings_{bucket_key}_ops") if away_off else None
    home_off_ops = home_off.get(f"innings_{bucket_key}_ops") if home_off else None
    away_off_wrc = away_off.get(f"innings_{bucket_key}_wrc_plus") if away_off else None
    home_off_wrc = home_off.get(f"innings_{bucket_key}_wrc_plus") if home_off else None

    print(f"    Offense:")
    print(f"      {away_team}: {fmt(away_off_rpg, 2)} R/G / OPS {fmt(away_off_ops, 3)} / wRC+ {fmt_int(away_off_wrc)}")
    print(f"      {home_team}: {fmt(home_off_rpg, 2)} R/G / OPS {fmt(home_off_ops, 3)} / wRC+ {fmt_int(home_off_wrc)}")


def render_game(game):
    away_team = game.get("away_team")
    home_team = game.get("home_team")
    away_sp = game.get("away_pitcher") or "TBD"
    home_sp = game.get("home_pitcher") or "TBD"
    venue = game.get("venue") or ""

    print("\n" + "=" * 78)
    print(f" {away_team} @ {home_team}")
    print(f" {venue}")
    print("=" * 78)
    spread = game.get("close_spread")
    if spread is None:
        spread = game.get("projected_spread")
    total = game.get("close_total") or game.get("projected_total")
    conf = game.get("signal_confluence_net")
    nrfi = game.get("nrfi_score")
    print(f"  Market: spread {fmt(spread, 1)} / total {fmt(total, 1)} | confluence net {conf if conf is not None else '—'} | NRFI {nrfi if nrfi is not None else '—'}")

    # Bullpen workload flags
    home_3d = game.get("home_bp_relievers_3d")
    away_3d = game.get("away_bp_relievers_3d")
    print(f"  Bullpen workload (last 3d): {away_team} {fmt_int(away_3d)} relievers{gassed_flag(away_3d)} | {home_team} {fmt_int(home_3d)} relievers{gassed_flag(home_3d)}")

    away_off = fetch_team_offense(away_team)
    home_off = fetch_team_offense(home_team)
    away_pen = fetch_bullpen(away_team)
    home_pen = fetch_bullpen(home_team)
    away_sp_data = fetch_pitcher(away_sp)
    home_sp_data = fetch_pitcher(home_sp)

    # Recency lens — informational only (not yet blended into projection;
    # awaiting backtest for proper weight). Surfaces hot/cold streaks the
    # season-long stats hide.
    def _recency_line(team_label, off):
        if not off:
            return None
        season_rpg = off.get('runs_per_game')
        l10_rpg = off.get('last10_runs_per_game')
        l10_diff = off.get('last10_run_diff')
        l10_n = off.get('last10_games_sampled')
        if l10_rpg is None or season_rpg is None:
            return f"    {team_label}: L10 data unavailable"
        delta = round(float(l10_rpg) - float(season_rpg), 2)
        flag = ''
        if abs(delta) >= 1.0:
            flag = ' 🔥 HOT' if delta > 0 else ' ❄️ COLD'
        elif abs(delta) >= 0.5:
            flag = ' (mildly hot)' if delta > 0 else ' (mildly cold)'
        diff_str = f"L10 run diff {fmt(l10_diff, 2)}" if l10_diff is not None else ''
        return f"    {team_label}: season {fmt(season_rpg, 2)} R/G | L10 {fmt(l10_rpg, 2)} R/G ({'+' if delta >= 0 else ''}{delta:.2f}){flag} | {diff_str} (n={fmt_int(l10_n)})"

    print(f"  ── RECENCY (last 10 games) ──")
    away_rec = _recency_line(away_team, away_off)
    home_rec = _recency_line(home_team, home_off)
    if away_rec: print(away_rec)
    if home_rec: print(home_rec)

    # Umpire signal (added 2026-05-10) — audit-anchored flags from mlb_umpires
    ump_name = game.get("umpire")
    if ump_name:
        ump_data = get_ump_stats(ump_name)
        if ump_data:
            ump_flags = ump_signal_summary(ump_data)
            if ump_flags:
                n = ump_data.get('games_sampled', 0)
                print(f"  ── UMPIRE: {ump_name} (n={n}) ──")
                for f in ump_flags:
                    print(f"    {f}")

    # Lineup degradation signal (added 2026-05-10) — confirmed starting 9
    # OPS vs season team wRC+. Flags when key bats are sitting and lineup
    # quality dropped meaningfully. Only fires when lineup_confirmed=True.
    if game.get("lineup_confirmed"):
        h_lineup_ops = game.get("home_lineup_ops")
        a_lineup_ops = game.get("away_lineup_ops")
        h_team_wrc = game.get("home_wrc_plus")
        a_team_wrc = game.get("away_wrc_plus")
        h_delta, h_flag = lineup_degradation_flag(h_lineup_ops, h_team_wrc)
        a_delta, a_flag = lineup_degradation_flag(a_lineup_ops, a_team_wrc)
        if (h_delta is not None and abs(h_delta) >= 5) or (a_delta is not None and abs(a_delta) >= 5):
            print(f"  ── LINEUP vs SEASON BASELINE ──")
            if a_delta is not None:
                sign = '+' if a_delta >= 0 else ''
                print(f"    {away_team}: starting 9 ≈ {sign}{a_delta} wRC+ pts vs season{(' — ' + a_flag) if a_flag else ''}")
            if h_delta is not None:
                sign = '+' if h_delta >= 0 else ''
                print(f"    {home_team}: starting 9 ≈ {sign}{h_delta} wRC+ pts vs season{(' — ' + h_flag) if h_flag else ''}")

    # Pitcher class projections (Phase A — 2026-05-10)
    # Each starter's expected IP/ER/Outs vs offenses similar in wRC+ to tonight's
    # opponent. Pulled from compute_pitcher_class_projections.py JSON cache.
    class_proj = getattr(render_game, '_class_proj_cache', None)
    if class_proj is None:
        class_proj = load_class_projections()
        render_game._class_proj_cache = class_proj
    if class_proj:
        away_proj = class_proj.get((away_sp or '').lower())
        home_proj = class_proj.get((home_sp or '').lower())
        away_opp_wrc = (home_off or {}).get('wrc_plus')
        home_opp_wrc = (away_off or {}).get('wrc_plus')
        away_bucket = opp_wrc_to_bucket(away_opp_wrc)
        home_bucket = opp_wrc_to_bucket(home_opp_wrc)

        def proj_line(label, sp, proj, opp_wrc, bucket):
            if not proj or not bucket:
                return f"    {label} ({sp}): no class projection available"
            classes = proj.get('classes', {})
            cls = classes.get(bucket)
            l7 = proj.get('l7_rolling')
            l7_str = ''
            if l7:
                l7_str = (f"\n      L7 rolling ({l7['n_starts']} starts): "
                          f"{l7['avg_bb']} BB / {l7['avg_hits']} H / {l7['avg_k']} K per start "
                          f"({l7['era']} ERA, {l7.get('whip', '—')} WHIP, {l7['bb_per_9']} BB/9, {l7['hits_per_9']} H/9)")
            if not cls:
                # Fall back to nearest bucket with data
                fallback = None
                for b in ('101_110', '91_100', '111_120', '111_120', 'le_90', 'ge_121'):
                    if b in classes:
                        fallback = (b, classes[b]); break
                if fallback:
                    fb_label, fc = fallback
                    return (f"    {label} ({sp}): no n≥2 sample at opp wRC+ {opp_wrc} "
                            f"({bucket}) — nearest class {fb_label}: {fc['avg_ip']} IP / "
                            f"{fc['avg_er']} ER / {fc['avg_bb']} BB / {fc.get('avg_hits', '—')} H (n={fc['n']})"
                            + l7_str)
                return f"    {label} ({sp}): no class data" + l7_str
            return (f"    {label} ({sp}) vs ~{opp_wrc} wRC+ class ({bucket}): "
                    f"{cls['avg_ip']} IP / {cls['avg_er']} ER / {cls['avg_bb']} BB / "
                    f"{cls.get('avg_hits', '—')} H / {cls['avg_k']} K "
                    f"(n={cls['n']} historical, ERA-in-class {cls.get('era_in_class', '—')})"
                    + l7_str)

        print(f"  ── PITCHER CLASS PROJECTIONS (vs offenses similar to tonight's opp) ──")
        print(proj_line(away_team, away_sp, away_proj, away_opp_wrc, away_bucket))
        print(proj_line(home_team, home_sp, home_proj, home_opp_wrc, home_bucket))

        # Pitcher prop board — line-by-line BB/Outs/K lean analysis from L7
        print(f"  ── PITCHER PROP BOARD (L7 lean at common book lines) ──")
        for team_label, sp, proj in [(away_team, away_sp, away_proj), (home_team, home_sp, home_proj)]:
            board = pitcher_prop_board(sp, proj)
            if board:
                print(f"    {team_label} ({sp}):")
                for line in board:
                    print(f"      {line}")

    # 1st inning specifically (NRFI-relevant — leadoff hitters skew bucket avg)
    if (away_off and away_off.get('inning_1_runs_per_game') is not None) or \
       (home_off and home_off.get('inning_1_runs_per_game') is not None):
        print(f"  ── 1ST INNING ONLY (NRFI lens) ──")
        if away_off and away_off.get('inning_1_runs_per_game') is not None:
            print(f"    {away_team}: {fmt(away_off.get('inning_1_runs_per_game'), 2)} R/G / OPS {fmt(away_off.get('inning_1_ops'), 3)} / wRC+ {fmt_int(away_off.get('inning_1_wrc_plus'))}")
        if home_off and home_off.get('inning_1_runs_per_game') is not None:
            print(f"    {home_team}: {fmt(home_off.get('inning_1_runs_per_game'), 2)} R/G / OPS {fmt(home_off.get('inning_1_ops'), 3)} / wRC+ {fmt_int(home_off.get('inning_1_wrc_plus'))}")

    for label, key in (("1-3", "1_3"), ("4-6", "4_6"), ("7-9 (bullpen)", "7_9")):
        render_bucket(label, away_team, home_team, away_off, home_off, away_pen, home_pen,
                      away_sp, home_sp, away_sp_data, home_sp_data, key)


def fetch_tier_rates():
    """Pull live tier calibration rates from latest computation."""
    rows = sb_get("mlb_tier_calibration", {
        "sport": "eq.mlb",
        "select": "tier,window_label,hits,total,hit_rate",
        "order": "computed_date.desc",
        "limit": "200",
    })
    if not rows:
        return {}
    # Keep most recent computed_date entries per tier+window
    seen = set()
    latest = {}
    for r in rows:
        key = (r.get("tier"), r.get("window_label"))
        if key in seen:
            continue
        seen.add(key)
        latest[key] = r
    return latest


def render_calibration_header(rates):
    """Print live tier calibration banner so we see calibration freshness."""
    if not rates:
        return
    print("\n" + "=" * 78)
    print(" 📊 LIVE TIER CALIBRATION (last 30 days)")
    print("=" * 78)
    spotlight = [
        ("nrfi_prime_90_94", "NRFI 90-94 PRIME"),
        ("nrfi_lean_70_79", "NRFI 70-79 mild lean"),
        ("nrfi_volatile_95plus", "NRFI 95+ volatile (skip)"),
        ("nrfi_60_69", "NRFI 60-69 (skip)"),
        ("yrfi_lean_le40", "YRFI lean ≤40"),
        ("spread_delta_ge2", "Spread Δ ≥2.0"),
    ]
    for tier_key, label in spotlight:
        r = rates.get((tier_key, "30d"))
        if r and r.get("total"):
            rate = float(r["hit_rate"]) * 100
            print(f"  {label:30s} {r['hits']}-{r['total']-r['hits']}  ({rate:.1f}%)")


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Missing SUPABASE_URL / SUPABASE_KEY env vars. Run from mlb_pipeline/ with .env present.")
        sys.exit(1)

    game_date = sys.argv[1] if len(sys.argv) > 1 else get_today_et()
    print(f"Inning-bucket scout report — {game_date}")

    # Live calibration header — shows audit-validated tier rates current as of
    # last audit_tier_calibration.py run (cron daily). Replaces stale memory.
    rates = fetch_tier_rates()
    render_calibration_header(rates)

    games = fetch_games(game_date)
    if not games:
        print(f"\nNo games found in mlb_game_context for {game_date}.")
        return
    print(f"\nFound {len(games)} games on slate.\n")

    for g in games:
        render_game(g)

    print("\n" + "=" * 78)
    print("Scout report complete. Reads above are season-long inning-bucket tendencies.")
    print("Single-game variance applies — these are tendencies, not predictions.")


if __name__ == "__main__":
    main()
