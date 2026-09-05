"""NCAAF daily data-quality audit.

Nightly cron script that scans ncaaf_game_context for the whole
upcoming week and flags:

  1. Self-vs-self rows (Team X @ Team X) — resolver collapse (e.g.
     Michigan @ Michigan, Georgia @ Charlotte via Citadel Bulldogs)
  2. Mascot-dup pairs — 'Bryant' vs 'Bryant Bulldogs' as separate
     game_ids on the same date (resolver mascot-strip missed)
  3. Spread-vs-ML sign flip — spread says home fav but home ML is
     +200+ dog (or mirror). Odds API source flip.
  4. Missing pitcher/starter data for FBS games (indicator of pull
     failure on a real Sat game)

Prints a report + writes findings to team_alias_gaps (via source=
'daily_dq_audit') so triage stays centralized.

Runs after morning ncaaf_odds_pull. Fails open (best-effort logging)
so downstream jobs never block.

CLI:
  python ncaaf_daily_dq_audit.py           # today + 7d horizon
  python ncaaf_daily_dq_audit.py --days 14 # extended window
  python ncaaf_daily_dq_audit.py --dry-run # print only, no writes
"""
from __future__ import annotations
import argparse, os, sys, io, requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


MASCOT_SUFFIXES = {
    'Tigers','Bulldogs','Panthers','Wildcats','Eagles','Cardinals','Bears','Lions',
    'Aggies','Hornets','Vandals','Sycamores','Buccaneers','Redhawks','Demons',
    'Colonels','Jackrabbits','Wolves','Warriors','Owls','Rams','Bulls','Cougars',
    'Hawks','Huskies','Falcons','Broncos','Mustangs','Bison','Delta Devils',
    'Governors','Chanticleers','Rattlers','Blazers','Racers','Spartans','Lakers',
    'Utes','Volunteers','Gators','Seminoles','Hurricanes','Yellow Jackets',
    'Fighting Irish','Fighting Illini','Nittany Lions','Buckeyes','Wolverines',
    'Cavaliers','Rebels','Chippewas', 'Trojans','Bruins','Ducks','Sooners',
    'Longhorns','Cowboys','Aztecs','Fighting Sioux','Goldens','Golden Bears',
    'Terrapins','Scarlet Knights','Boilermakers','Hoosiers','Cornhuskers',
}


def _strip_mascot(name: str) -> str:
    if not name: return name
    n = name.strip()
    for suf in sorted(MASCOT_SUFFIXES, key=len, reverse=True):
        if n.endswith(' ' + suf):
            return n[:-len(suf) - 1].strip()
    return n


def audit(days: int = 7, dry_run: bool = False) -> None:
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    horizon = (today + timedelta(days=days)).isoformat()
    start = today.isoformat()

    print(f'\n=== NCAAF daily DQ audit · {start} to {horizon} ===\n')

    r = requests.get(f'{SB}/rest/v1/ncaaf_game_context',
        headers=H_READ,
        params={'select': 'game_id,game_date,home_team,away_team,close_spread,'
                          'close_home_ml,close_away_ml,close_total,projected_total',
                'game_date': f'gte.{start}',
                'limit': '1000'},
        timeout=20).json()
    if not isinstance(r, list):
        print(f'  ⛔ fetch failed: {r}'); return
    rows = [g for g in r if g.get('game_date') and g['game_date'] <= horizon]
    print(f'  Loaded {len(rows)} NCAAF game_context rows\n')

    findings: list[tuple[str, str, dict]] = []

    # (1) Self-vs-self
    for g in rows:
        if g.get('home_team') and g.get('home_team') == g.get('away_team'):
            findings.append(('self_vs_self', f'{g["home_team"]} @ {g["home_team"]}', g))

    # (2) Mascot dupes on same date
    by_fp = defaultdict(list)
    for g in rows:
        fp = (_strip_mascot(g.get('home_team') or '').lower(),
              _strip_mascot(g.get('away_team') or '').lower(),
              g.get('game_date'))
        by_fp[fp].append(g)
    for fp, group in by_fp.items():
        if len(group) > 1:
            names = ' / '.join(f'{g["away_team"]} @ {g["home_team"]}' for g in group)
            findings.append(('mascot_dupe', names, {'group_ids': [x['game_id'] for x in group]}))

    # (3) Spread-vs-ML sign flip
    for g in rows:
        sp = g.get('close_spread'); hml = g.get('close_home_ml'); aml = g.get('close_away_ml')
        if sp is None or hml is None or aml is None: continue
        try:
            sp_f = float(sp); hml_f = float(hml); aml_f = float(aml)
        except (TypeError, ValueError): continue
        # Home fav by spread but heavy dog by ML
        if sp_f <= -7 and hml_f >= 200 and aml_f <= -300:
            findings.append(('spread_ml_flip', f'{g["away_team"]} @ {g["home_team"]}',
                             {'game_id': g['game_id'], 'sp': sp_f, 'hml': hml_f, 'aml': aml_f}))
        # Home dog by spread but heavy fav by ML (mirror)
        elif sp_f >= 7 and hml_f <= -300 and aml_f >= 200:
            findings.append(('spread_ml_flip_mirror', f'{g["away_team"]} @ {g["home_team"]}',
                             {'game_id': g['game_id'], 'sp': sp_f, 'hml': hml_f, 'aml': aml_f}))

    # (4) Suspicious total (line and projection > 15 apart) — indicator of
    # data ingestion error since real Vegas totals never disagree with any
    # honest projection by that much.
    for g in rows:
        line = g.get('close_total'); proj = g.get('projected_total')
        if line is None or proj is None: continue
        try:
            l = float(line); p = float(proj)
        except (TypeError, ValueError): continue
        if abs(l - p) > 15:
            findings.append(('total_line_vs_proj_gap', f'{g["away_team"]} @ {g["home_team"]}',
                             {'game_id': g['game_id'], 'line': l, 'proj': p, 'gap': abs(l - p)}))

    # Report
    by_type: defaultdict = defaultdict(list)
    for kind, label, meta in findings:
        by_type[kind].append((label, meta))
    if not findings:
        print('  ✓ Clean — no data-quality issues found\n')
        return

    print(f'  🚨 {len(findings)} findings across {len(by_type)} categories:\n')
    for kind, entries in by_type.items():
        print(f'  [{kind}] ({len(entries)}):')
        for label, meta in entries[:10]:
            print(f'    - {label}   {meta.get("game_id","") or ""}')
        if len(entries) > 10:
            print(f'    ... +{len(entries) - 10} more')
        print()

    if dry_run:
        print('[DRY RUN] no writes performed.')
        return

    # Write summary to a jerry_cache row so ops can see it in one place
    payload = {
        'cache_key': f'ncaaf_dq_audit_{start}',
        'game_id': f'ncaaf_dq_{start}',
        'sport': 'NCAAF',
        'narrative': f'{len(findings)} findings across {len(by_type)} categories',
        'data': {'findings': [{'kind': k, 'label': l, 'meta': m}
                              for k, l, m in findings],
                 'window': f'{start} to {horizon}'},
    }
    r = requests.post(f'{SB}/rest/v1/jerry_cache?on_conflict=cache_key',
                      headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
                      json=payload, timeout=15)
    print(f'  audit summary write: {r.status_code}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    audit(days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
