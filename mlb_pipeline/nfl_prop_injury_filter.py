"""NFL prop injury filter — post-generation demotion pass (2026-09-10).

Root cause user flagged: Brock Bowers appeared as PRIME reception_yds_under
prop on the LV @ MIA card despite being listed 'Doubtful' in nfl_injuries
since 9/09. Investigation showed nfl_generate_props reads ctx.panel_injury_outs
(an aggregate INT count, currently 0 for every game) and only downgrades
whole-team scoring when the count is ≥3. Individual player status is never
consulted. So an injured skill player's prop still ships as PRIME.

Fix strategy — SAFE (post-generation):
Run this filter AFTER nfl_generate_props writes rows to nfl_pipeline_props.
Read current-week rows, look up each player in nfl_injuries (freshest
report_date per player+team), then demote by status:

  status='Out'        → tier='SKIP', conviction=0, signal 'injury_out'
  status='Doubtful'   → tier='LEAN' (if higher), conviction≤60, signal 'injury_doubtful'
  status='Questionable' → tier unchanged, conviction≤85, signal 'injury_questionable'
  status='Full'/None  → no change

Non-invasive: the generator is untouched, tests unchanged, rollback is
"don't run this script." Wire into the NFL workflow after generator.

CLI:
    python nfl_prop_injury_filter.py --date 2026-09-13
    python nfl_prop_injury_filter.py --window 7        # all games in next 7 days
    python nfl_prop_injury_filter.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


# Demotion policy — tier + conviction cap + signal key added to reason.
# 'Full'/None = no injury impact, skip.
DEMOTION = {
    'out':          {'tier': 'SKIP', 'conv_cap': 0,   'signal': 'injury_out',
                     'reason': '{player} listed OUT — prop suppressed by injury filter'},
    'doubtful':     {'tier': 'LEAN', 'conv_cap': 60,  'signal': 'injury_doubtful',
                     'reason': '{player} listed DOUBTFUL — tier capped at LEAN by injury filter'},
    'questionable': {'tier': None,   'conv_cap': 85,  'signal': 'injury_questionable',
                     'reason': '{player} listed QUESTIONABLE — conviction capped at 85 pending inactives'},
}
# Tier severity for the "only demote, never promote" comparison.
_TIER_RANK = {'PRIME': 4, 'STRONG': 3, 'LEAN': 2, 'LIGHT': 1, 'SKIP': 0, 'COVERAGE': 0}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def load_current_injuries() -> dict[str, dict]:
    """Return {player_name_lower + team: {status, body_part, updated_at}}.

    Uses the FRESHEST report_date per player+team so a Wed status update
    beats a Mon status. Filters to status in Out/Doubtful/Questionable —
    Full/None mean "no impact" and we skip them.
    """
    r = requests.get(f'{SB}/rest/v1/nfl_injuries', headers=H_READ,
        params={'select': 'player_name,team,injury_status,body_part,report_date,updated_at',
                'order': 'report_date.desc',
                'limit': '5000'}, timeout=20)
    if r.status_code != 200:
        print(f'  ✗ nfl_injuries fetch {r.status_code}'); return {}
    per_key = {}
    for row in (r.json() or []):
        if not isinstance(row, dict): continue
        status = str(row.get('injury_status') or '').lower().strip()
        if status not in DEMOTION: continue
        name = str(row.get('player_name') or '').strip()
        team = str(row.get('team') or '').strip()
        if not name or not team: continue
        key = f'{name.lower()}|{team}'
        # Take freshest per key
        if key not in per_key or (row.get('report_date') or '') > (per_key[key].get('report_date') or ''):
            per_key[key] = row
    return per_key


def load_props(date_from: str, date_to: str) -> list[dict]:
    r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props', headers=H_READ,
        params={'game_date': f'gte.{date_from}',
                'and': f'(game_date.lte.{date_to})',
                'select': 'id,game_date,game_id,player_name,player_team,prop_type,'
                          'tier,conviction,signals',
                'limit': '5000'}, timeout=25)
    if r.status_code != 200:
        print(f'  ✗ nfl_pipeline_props fetch {r.status_code}: {r.text[:200]}'); return []
    return r.json() or []


def apply_demotion(row: dict, injury: dict) -> dict | None:
    """Return the PATCH payload (or None if no change needed)."""
    status = str(injury.get('injury_status') or '').lower().strip()
    policy = DEMOTION.get(status)
    if not policy: return None
    current_tier = str(row.get('tier') or '').upper()
    current_conv = int(row.get('conviction') or 0)
    new_tier = current_tier
    new_conv = current_conv
    # Tier demotion — only step DOWN, never up
    if policy['tier']:
        target_rank = _TIER_RANK.get(policy['tier'], 0)
        current_rank = _TIER_RANK.get(current_tier, 0)
        if target_rank < current_rank:
            new_tier = policy['tier']
    # Conviction cap — only reduce
    if new_conv > policy['conv_cap']:
        new_conv = policy['conv_cap']
    if new_tier == current_tier and new_conv == current_conv:
        return None
    # Merge into signals JSONB. Preserve existing keys, add injury_* one.
    signals = row.get('signals') or {}
    if isinstance(signals, str):
        try: signals = json.loads(signals)
        except (TypeError, ValueError): signals = {}
    if not isinstance(signals, dict): signals = {}
    signals[policy['signal']] = policy['reason'].format(player=row.get('player_name') or 'Player')
    body_part = injury.get('body_part')
    if body_part:
        signals['injury_body_part'] = body_part
    return {'tier': new_tier, 'conviction': new_conv, 'signals': signals}


def run(date_from: str, date_to: str, dry_run: bool = False) -> None:
    print(f'=== NFL prop injury filter · {date_from} → {date_to}{" (DRY)" if dry_run else ""} ===')
    injuries = load_current_injuries()
    print(f'  injuries in scope (Out/Doubtful/Questionable): {len(injuries)}')
    if len(injuries) == 0:
        print('  nothing to do'); return
    props = load_props(date_from, date_to)
    print(f'  props in scope: {len(props)}')
    demoted = 0; skipped = 0
    for p in props:
        name = str(p.get('player_name') or '').strip()
        team = str(p.get('player_team') or '').strip()
        if not name or not team: continue
        key = f'{name.lower()}|{team}'
        inj = injuries.get(key)
        if not inj: continue
        patch = apply_demotion(p, inj)
        if not patch: continue
        if dry_run:
            print(f'  [DRY] {name} ({team}) {inj.get("injury_status")}: '
                  f'{p.get("tier")} conv={p.get("conviction")} → '
                  f'{patch.get("tier")} conv={patch.get("conviction")}')
            demoted += 1; continue
        # PATCH the row by id
        pr = requests.patch(f'{SB}/rest/v1/nfl_pipeline_props',
            headers=H_WRITE, params={'id': f'eq.{p["id"]}'},
            json=patch, timeout=15)
        if pr.status_code in (200, 204):
            demoted += 1
            print(f'  ✓ demoted {name} ({team}) {inj.get("injury_status")}: '
                  f'{p.get("tier")} → {patch.get("tier")} · conv {p.get("conviction")} → {patch.get("conviction")}')
        else:
            skipped += 1
            print(f'  ✗ patch fail {name}: {pr.status_code} {pr.text[:100]}')
    print(f'\n=== done · demoted={demoted}  errors={skipped} ===')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='specific date YYYY-MM-DD')
    p.add_argument('--window', type=int, default=7, help='days forward from today (default 7)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    if args.date:
        date_from = date_to = args.date
    else:
        today = today_et()
        end = (datetime.fromisoformat(today) + timedelta(days=args.window)).date().isoformat()
        date_from = today; date_to = end
    run(date_from, date_to, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
