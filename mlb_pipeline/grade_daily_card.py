"""Auto-grade the day's picks — sides / totals / props / primary_plays.

Runs late night (via cron) or on-demand. Pulls final scores from
mlb_game_results, evaluates every pick against the outcome, writes
one row per pick to daily_grades. Downstream morning audit then just
reads the graded table — no manual pull needed.

Grades:
  - PRIMARY_PLAYS from mlb_game_context.primary_play — the model's
    surfaced recommendations
  - Pipeline props from mlb_pipeline_props (already have .result field,
    just aggregates by tier)
  - MC HIGH-CONF chips — did they hit ML SU?
  - NRFI ensemble picks — did NRFI/YRFI fire correctly?
  - Consensus fade flags — did the fade side (or aligned side) win?

Writes to: daily_grades table (auto-created if missing? — assume
migration ships separately, this script only writes when table exists)

Usage:
    python grade_daily_card.py              # grade today
    python grade_daily_card.py --date 2026-07-25
    python grade_daily_card.py --dry-run
"""
import argparse, os, requests, sys, json
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from datetime import datetime, timedelta, timezone
env = Path('.env').read_text()
for line in env.split('\n'):
    if '=' in line and not line.startswith('#'):
        k,v = line.split('=',1); os.environ[k.strip()] = v.strip()
url = os.environ['SUPABASE_URL']; key = os.environ['SUPABASE_KEY']
h = {'apikey': key, 'Authorization': f'Bearer {key}'}
hw = {**h, 'Content-Type':'application/json','Prefer':'resolution=merge-duplicates,return=minimal'}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _yesterday_et() -> str:
    """Default date for the grader — yesterday's ET slate is what the
    morning resolver just finished resolving. Today's slate hasn't been
    played yet (grader would find all PENDING). This is the actual
    default we want."""
    return (datetime.now(timezone.utc) - timedelta(hours=4, days=1)).strftime('%Y-%m-%d')


def _grade_side(pp_type: str, pp_label: str, ctx: dict, res: dict) -> str:
    """Grade a primary_play side pick. Returns W/L/P/PENDING."""
    if res.get('home_score') is None or res.get('away_score') is None:
        return 'PENDING'
    home = ctx.get('home_team')
    away = ctx.get('away_team')
    label = (pp_label or '').lower()
    # Determine which team the pick was on
    picked_home = home and home.lower() in label
    picked_away = away and away.lower() in label
    if not (picked_home or picked_away):
        return 'UNCLEAR'
    if pp_type == 'ml':
        won = res.get('home_win') if picked_home else (not res.get('home_win'))
        return 'W' if won else 'L'
    # Not currently handling RL/spread from primary_play labels
    return 'UNCLEAR'


def _grade_total(pp_type: str, pp_label: str, ctx: dict, res: dict) -> str:
    """Grade a primary_play total pick (over/under)."""
    tr = res.get('total_result')
    if not tr:
        return 'PENDING'
    tr_lower = tr.lower()
    if pp_type == 'over':
        return 'W' if tr_lower == 'over' else ('P' if tr_lower == 'push' else 'L')
    if pp_type == 'under':
        return 'W' if tr_lower == 'under' else ('P' if tr_lower == 'push' else 'L')
    return 'UNCLEAR'


def _grade_nrfi_ensemble(pick: str, ctx: dict) -> str:
    """Grade NRFI/YRFI ensemble pick."""
    actual = ctx.get('nrfi_result')
    if not actual or actual not in ('NRFI', 'YRFI'):
        return 'PENDING'
    if pick not in ('NRFI', 'YRFI'):
        return 'UNCLEAR'
    return 'W' if pick == actual else 'L'


def _grade_mc_high_conf(side: str, ctx: dict, res: dict) -> str:
    """Grade the MC HIGH-CONF ML pick (side = HOME/AWAY)."""
    if res.get('home_score') is None:
        return 'PENDING'
    if side == 'HOME':
        return 'W' if res.get('home_win') else 'L'
    if side == 'AWAY':
        return 'W' if not res.get('home_win') else 'L'
    return 'UNCLEAR'


def run(date_str: str, dry_run: bool = False) -> None:
    print(f'=== grade_daily_card · {date_str} ===')
    ctxs = requests.get(f'{url}/rest/v1/mlb_game_context?game_date=eq.{date_str}&select=*', headers=h, timeout=15).json()
    res_rows = requests.get(f'{url}/rest/v1/mlb_game_results?game_date=eq.{date_str}&select=game_id,home_team,away_team,home_score,away_score,home_win,total_result,spread_result', headers=h, timeout=15).json()
    res_map = {r['game_id']: r for r in res_rows if isinstance(r, dict)}
    print(f'  context rows: {len(ctxs)}   result rows: {len(res_map)}')

    grades = []
    tally = {'W':0,'L':0,'P':0,'PENDING':0,'UNCLEAR':0}
    tally_by_type = {}

    for c in ctxs:
        gid = c['game_id']
        matchup = f"{c['away_team']} @ {c['home_team']}"
        r = res_map.get(gid, {})

        # 1. Primary play
        pp = c.get('primary_play') or {}
        if isinstance(pp, dict) and pp.get('type'):
            pp_type = pp['type']
            if pp_type == 'ml':
                grade = _grade_side(pp_type, pp.get('label',''), c, r)
            elif pp_type in ('over','under'):
                grade = _grade_total(pp_type, pp.get('label',''), c, r)
            else:
                grade = 'UNCLEAR'
            grades.append({
                'game_date': date_str, 'game_id': gid, 'pick_type': 'primary_play',
                'pick_label': f"{pp.get('tier')} · {pp.get('label')}",
                'matchup': matchup, 'grade': grade,
            })
            tally[grade] += 1
            tally_by_type.setdefault('primary_play', {}).setdefault(grade, 0)
            tally_by_type['primary_play'][grade] += 1

        # 2. MC HIGH-CONF chip
        if c.get('mc_high_conf_flag'):
            side = c.get('mc_high_conf_side')
            grade = _grade_mc_high_conf(side, c, r)
            grades.append({
                'game_date': date_str, 'game_id': gid, 'pick_type': 'mc_high_conf',
                'pick_label': f"MC HC {side} {c.get('mc_high_conf_pct',0):.0%}",
                'matchup': matchup, 'grade': grade,
            })
            tally[grade] += 1
            tally_by_type.setdefault('mc_high_conf', {}).setdefault(grade, 0)
            tally_by_type['mc_high_conf'][grade] += 1

        # 3. NRFI ensemble
        ens_pick = c.get('nrfi_ensemble_pick')
        ens_tier = c.get('nrfi_ensemble_tier')
        if ens_pick and ens_tier and ens_tier != 'SKIP':
            grade = _grade_nrfi_ensemble(ens_pick, c)
            grades.append({
                'game_date': date_str, 'game_id': gid, 'pick_type': 'nrfi_ensemble',
                'pick_label': f"{ens_tier} {ens_pick}",
                'matchup': matchup, 'grade': grade,
            })
            tally[grade] += 1
            tally_by_type.setdefault('nrfi_ensemble', {}).setdefault(grade, 0)
            tally_by_type['nrfi_ensemble'][grade] += 1

    # 4. Pipeline props — aggregate from mlb_pipeline_props (already graded by resolver)
    props = requests.get(f'{url}/rest/v1/mlb_pipeline_props?game_date=eq.{date_str}&tier=in.(PRIME,STRONG)&select=player_name,prop_type,direction,prop_line,tier,result,final_value,matchup', headers=h, timeout=15).json()
    for p in props:
        if not isinstance(p, dict): continue
        result = (p.get('result') or 'PENDING').upper()
        # Normalize
        if result == 'WIN': grade = 'W'
        elif result == 'LOSS': grade = 'L'
        elif result == 'PUSH': grade = 'P'
        else: grade = 'PENDING'
        grades.append({
            'game_date': date_str, 'game_id': None, 'pick_type': f'prop_{p.get("tier","").lower()}',
            'pick_label': f"{p.get('player_name')} {p.get('prop_type')} {p.get('direction')} {p.get('prop_line')}",
            'matchup': p.get('matchup') or '',
            'grade': grade,
        })
        tally[grade] += 1
        pt = f"prop_{p.get('tier','').lower()}"
        tally_by_type.setdefault(pt, {}).setdefault(grade, 0)
        tally_by_type[pt][grade] += 1

    # Print summary
    print(f'\n=== Grade summary ({len(grades)} picks) ===')
    print(f'  overall: W={tally["W"]}  L={tally["L"]}  P={tally["P"]}  PENDING={tally["PENDING"]}  UNCLEAR={tally["UNCLEAR"]}')
    for typ, sub in tally_by_type.items():
        n_wl = sub.get('W',0) + sub.get('L',0)
        pct = (100*sub.get('W',0)/n_wl) if n_wl else 0
        print(f"    {typ:<20}  W={sub.get('W',0)} L={sub.get('L',0)} P={sub.get('P',0)} PENDING={sub.get('PENDING',0)}  ({pct:.0f}%)")

    # Write to daily_grades table (if it exists)
    if dry_run:
        print(f'\n[DRY] would write {len(grades)} grade rows')
        return
    resp = requests.post(f'{url}/rest/v1/daily_grades?on_conflict=game_date,game_id,pick_type,pick_label', headers=hw, json=grades)
    if resp.status_code in (200, 201, 204):
        print(f'\n✓ wrote {len(grades)} grade rows to daily_grades')
    elif resp.status_code == 404:
        print(f'\n⚠ daily_grades table not found — need migration. Grades computed but not persisted.')
    else:
        print(f'\n⚠ upsert failed {resp.status_code}: {resp.text[:200]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(args.date or _yesterday_et(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
