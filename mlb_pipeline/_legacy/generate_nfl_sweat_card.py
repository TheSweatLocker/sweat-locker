"""Generate NFL weekly content card (markdown) from pipeline output.

Reads jerry_cache.nfl_weekly_card_{season}_w{week} produced by
nfl_weekly_card.py, formats it into a copy/paste markdown file at
content/nfl_{season}_W{week}_card.md — TikTok short + Facebook long
formats + weekly parlay + transparency block.

Runs after nfl_weekly_card.py in the NFL cron so by Thursday morning
the post is drafted.

Usage:
    python generate_nfl_sweat_card.py                 # current week
    python generate_nfl_sweat_card.py --season 2026 --week 1
    python generate_nfl_sweat_card.py --dry-run       # print, no file write
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def sb_get(table: str, params: dict) -> list:
    qs = urllib.parse.urlencode(params, safe=',.()')
    url = f'{SB}/rest/v1/{table}?{qs}'
    req = urllib.request.Request(
        url,
        headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'  Supabase {table} error: {e}')
        return []


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def detect_season_week() -> tuple:
    """Pick the upcoming NFL week from the closest game in nfl_game_context."""
    today = _et_now().date().isoformat()
    horizon = (_et_now() + timedelta(days=10)).date().isoformat()
    rows = sb_get('nfl_game_context', {
        'game_date': f'gte.{today}',
        'game_date': f'lte.{horizon}',   # note: second key wins in urlencode; both filters applied by Supabase
        'select': 'season,week',
        'order': 'game_date.asc',
        'limit': '1',
    })
    if rows:
        return rows[0].get('season'), rows[0].get('week')
    return None, None


def fetch_weekly_card(season: int, week: int) -> Optional[dict]:
    cache_key = f'nfl_weekly_card_{season}_w{week}'
    rows = sb_get('jerry_cache', {
        'cache_key': f'eq.{cache_key}',
        'select': 'data',
    })
    if not rows:
        return None
    return rows[0].get('data')


def _fmt_matchup(matchup: str) -> str:
    return matchup.replace(' @ ', ' @ ') if matchup else '—'


def format_card(card: dict) -> str:
    """Format the jerry_cache payload into a markdown content file."""
    season = card.get('season')
    week = card.get('week_number')
    mode = card.get('mode') or 'full'
    lock = card.get('lock_of_week') or {}
    dawg = card.get('underdog_of_week') or {}
    parlay = card.get('weekly_parlay') or {}
    unders = card.get('outdoor_under_angles') or []
    skips = card.get('skip_alerts') or []
    slate = card.get('slate') or []
    audit = card.get('audit_cohort_summary') or {}
    gen_at = card.get('generated_at') or ''

    lines = []
    lines.append(f'# NFL Week {week} Card — {season} Season')
    lines.append('')
    lines.append(f'_Auto-generated {gen_at[:19]} · mode: {mode}_')
    lines.append('')
    lines.append('---')
    lines.append('')

    # LOCK
    if lock:
        lines.append('## 🔒 LOCK OF THE WEEK')
        lines.append(f'**{_fmt_matchup(lock.get("matchup"))}**')
        lines.append(f'**Pick:** {lock.get("pick")}')
        lines.append(f'**Tier:** {lock.get("tier")}  ·  Conviction {lock.get("conviction")}/100')
        if lock.get('audited_rate'):
            lines.append(f'**Audit:** {lock["audited_rate"]}% on {lock.get("audited_n")} historical games')
        if lock.get('rationale'):
            lines.append(f'_{lock["rationale"]}_')
        lines.append('')
    else:
        lines.append('## 🔒 LOCK OF THE WEEK')
        lines.append('_No lock this week — audit thresholds not met._')
        lines.append('')

    # UNDERDOG
    if dawg and (not lock or dawg.get('game_id') != lock.get('game_id')):
        lines.append('## 🐕 UNDERDOG OF THE WEEK')
        lines.append(f'**{_fmt_matchup(dawg.get("matchup"))}**')
        lines.append(f'**Pick:** {dawg.get("pick")}  ·  Tier {dawg.get("tier")}')
        if dawg.get('audited_rate'):
            lines.append(f'_Home-dog cohort {dawg["audited_rate"]}% on {dawg.get("audited_n")} games (2022-2025)_')
        lines.append('')

    # OUTDOOR UNDER ANGLES
    if unders:
        lines.append('## ⛈ WEATHER-DRIVEN UNDER LEANS')
        for u in unders[:3]:
            lines.append(f'- **{_fmt_matchup(u.get("matchup"))}** — {u.get("pick")} ({u.get("tier")})')
            lines.append(f'  _{u.get("rationale")}_')
        lines.append('')

    # PARLAY
    if parlay and parlay.get('legs'):
        lines.append(f'## 🎰 WEEKLY PARLAY — {parlay.get("leg_count")} legs')
        for leg in parlay['legs']:
            rate = leg.get('audited_rate')
            rate_note = f' _(cohort {rate}%)_' if rate else ''
            lines.append(f'- **{_fmt_matchup(leg.get("matchup"))}** — {leg.get("pick")}{rate_note}')
        if parlay.get('combined_rationale'):
            lines.append(f'_{parlay["combined_rationale"]}_')
        lines.append('')

    # SKIP ALERTS
    if skips:
        lines.append('## 🚫 SKIP — Traps the model rejects')
        for s in skips[:4]:
            lines.append(f'- **{_fmt_matchup(s.get("matchup"))}** — {s.get("message")}')
        lines.append('')

    # SLATE OVERVIEW
    if slate:
        lines.append(f'## 📅 FULL WEEK {week} SLATE ({len(slate)} games)')
        for g in slate[:16]:
            kickoff = g.get('kickoff') or ''
            sp = g.get('spread'); tot = g.get('total')
            div = ' 🏈DIV' if g.get('div_game') else ''
            roof = f' {g["roof"]}' if g.get('roof') else ''
            lines.append(f'- {kickoff}  ·  **{_fmt_matchup(g.get("matchup"))}**  ·  spread {sp}  total {tot}{div}{roof}')
        lines.append('')

    # AUDIT TRANSPARENCY BLOCK
    if audit:
        lines.append('## 📊 AUDIT COHORTS (live 30d)')
        for tier, v in list(audit.items())[:6]:
            hp = v.get('hit_pct'); n = v.get('sample_n')
            if hp is not None and n:
                lines.append(f'- {tier.replace("_", " ").title()}: **{hp}%** on n={n}')
        lines.append('')

    lines.append('---')
    lines.append('')

    # TIKTOK SHORT
    lines.append('## 📱 TIKTOK / IG SHORT (copy/paste, ~30 sec)')
    lines.append('')
    lines.append('> NFL Week ' + str(week) + ' from The Sweat Locker.')
    lines.append('> ')
    if lock:
        lines.append(f'> 🔒 **Lock:** {lock.get("pick")} ({_fmt_matchup(lock.get("matchup"))})')
    if dawg and (not lock or dawg.get('game_id') != lock.get('game_id')):
        lines.append(f'> 🐕 **Dawg:** {dawg.get("pick")}')
    if parlay:
        lines.append(f'> 🎰 **Parlay:** {parlay.get("leg_count")}-leg conviction ticket')
    lines.append('> ')
    lines.append('> Tracking every play. Receipts on Tuesday.')
    lines.append('')
    lines.append('---')
    lines.append('')

    # FACEBOOK LONG
    lines.append('## 📘 FACEBOOK / LONG-FORM (~150 words)')
    lines.append('')
    lines.append(f'> **NFL Week {week} card from The Sweat Locker model:**')
    lines.append('> ')
    if lock:
        lines.append(f'> 🔒 **LOCK OF THE WEEK:** {_fmt_matchup(lock.get("matchup"))}  ·  **Pick:** {lock.get("pick")}  ·  **Tier:** {lock.get("tier")}')
        if lock.get('rationale'):
            lines.append(f'> _{lock["rationale"]}_')
        lines.append('> ')
    if dawg and (not lock or dawg.get('game_id') != lock.get('game_id')):
        lines.append(f'> 🐕 **UNDERDOG:** {_fmt_matchup(dawg.get("matchup"))} — {dawg.get("pick")}')
        lines.append('> ')
    if parlay:
        lines.append(f'> 🎰 **{parlay.get("leg_count")}-LEG WEEKLY PARLAY** — no two legs share a game (correlation control).')
        lines.append('> ')
    if skips:
        lines.append('> 🚫 **WHAT WE\'RE NOT BETTING (and why):**')
        for s in skips[:2]:
            lines.append(f'> - {s.get("message")}')
        lines.append('> ')
    lines.append('> Posting transparently. Every pick tracked.')
    lines.append('')

    return '\n'.join(lines)


def run(season: Optional[int] = None, week: Optional[int] = None,
        dry_run: bool = False) -> None:
    if season is None or week is None:
        s, w = detect_season_week()
        season = season or s
        week = week or w
    if not season or not week:
        print('  ✗ Could not detect season/week — no upcoming games in nfl_game_context')
        return

    print(f'=== NFL sweat card · season {season} · Week {week} ===')

    card = fetch_weekly_card(season, week)
    if not card:
        print(f'  ✗ No jerry_cache entry for nfl_weekly_card_{season}_w{week}')
        print(f'    Run nfl_weekly_card.py first to populate it.')
        return

    md = format_card(card)
    if dry_run:
        print(md)
        return

    # Write to content/nfl_YYYY_WNN_card.md
    content_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'content')
    os.makedirs(content_dir, exist_ok=True)
    filename = f'nfl_{season}_W{week:02d}_card.md'
    filepath = os.path.join(content_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(md)
    print(f'✓ wrote {filepath} ({len(md)} chars)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=None)
    ap.add_argument('--week', type=int, default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(season=args.season, week=args.week, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
