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


def fetch_games(game_date):
    return sb_get(
        "mlb_game_context",
        {
            "game_date": f"eq.{game_date}",
            "select": "away_team,home_team,home_pitcher,away_pitcher,venue,close_spread,close_total,projected_spread,projected_total,signal_confluence_net,nrfi_score,home_bp_relievers_3d,away_bp_relievers_3d",
        },
    )


def fetch_pitcher(name):
    if not name:
        return {}
    rows = sb_get(
        "mlb_pitcher_stats",
        {
            "player_name": f"eq.{name}",
            "select": "player_name,xera,k_pct,first_inning_era,first_inning_whip,innings_1_3_era,innings_1_3_whip,innings_1_3_k_pct,innings_1_3_ip,innings_4_6_era,innings_4_6_whip,innings_4_6_k_pct,innings_4_6_ip,innings_7_9_era,innings_7_9_whip,innings_7_9_k_pct,innings_7_9_ip,last_3_era",
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
            "select": "team,wrc_plus,ops,k_pct,runs_per_game,last10_runs_per_game,last10_runs_allowed,last10_run_diff,last10_games_sampled,innings_1_3_runs_per_game,innings_1_3_ops,innings_1_3_wrc_plus,innings_1_3_k_pct,innings_4_6_runs_per_game,innings_4_6_ops,innings_4_6_wrc_plus,innings_4_6_k_pct,innings_7_9_runs_per_game,innings_7_9_ops,innings_7_9_wrc_plus,innings_7_9_k_pct",
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
            "select": "team,bullpen_era,save_pct,pitching_1_3_era,pitching_1_3_whip,pitching_1_3_k_pct,pitching_4_6_era,pitching_4_6_whip,pitching_4_6_k_pct,pitching_7_9_era,pitching_7_9_whip,pitching_7_9_k_pct,pitching_7_9_ip",
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

    print(f"    Starter pitching:")
    print(f"      {away_team} ({away_sp_label}): ERA {fmt(away_pitch_era)} / WHIP {fmt(away_pitch_whip)} / K% {fmt(away_pitch_kpct, 1)} ({fmt(away_pitch_ip, 1)} IP sample)")
    print(f"      {home_team} ({home_sp_label}): ERA {fmt(home_pitch_era)} / WHIP {fmt(home_pitch_whip)} / K% {fmt(home_pitch_kpct, 1)} ({fmt(home_pitch_ip, 1)} IP sample)")

    # Team-level pitching from bullpen_stats (whole staff blended for 1-3/4-6, ~bullpen for 7-9)
    away_team_era = away_pen.get(f"pitching_{bucket_key}_era") if away_pen else None
    home_team_era = home_pen.get(f"pitching_{bucket_key}_era") if home_pen else None
    pen_label = "Bullpen" if bucket_key == "7_9" else "Team pitching (blended)"
    print(f"    {pen_label} season-long:")
    print(f"      {away_team}: ERA {fmt(away_team_era)} / WHIP {fmt(away_pen.get(f'pitching_{bucket_key}_whip'))} / K% {fmt(away_pen.get(f'pitching_{bucket_key}_k_pct'), 1)}")
    print(f"      {home_team}: ERA {fmt(home_team_era)} / WHIP {fmt(home_pen.get(f'pitching_{bucket_key}_whip'))} / K% {fmt(home_pen.get(f'pitching_{bucket_key}_k_pct'), 1)}")

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

    for label, key in (("1-3", "1_3"), ("4-6", "4_6"), ("7-9 (bullpen)", "7_9")):
        render_bucket(label, away_team, home_team, away_off, home_off, away_pen, home_pen,
                      away_sp, home_sp, away_sp_data, home_sp_data, key)


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Missing SUPABASE_URL / SUPABASE_KEY env vars. Run from mlb_pipeline/ with .env present.")
        sys.exit(1)

    game_date = sys.argv[1] if len(sys.argv) > 1 else get_today_et()
    print(f"Inning-bucket scout report — {game_date}")
    games = fetch_games(game_date)
    if not games:
        print(f"No games found in mlb_game_context for {game_date}.")
        return
    print(f"Found {len(games)} games on slate.\n")

    for g in games:
        render_game(g)

    print("\n" + "=" * 78)
    print("Scout report complete. Reads above are season-long inning-bucket tendencies.")
    print("Single-game variance applies — these are tendencies, not predictions.")


if __name__ == "__main__":
    main()
