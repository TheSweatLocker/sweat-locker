"""Head-to-head: Panel projection vs my 'math' projection on 981 historical props.

For each pitcher prop with both a panel projection (_projected_er etc.)
and the underlying stats (xera + L3 ERA), compute:
  - Panel projection error
  - Math projection error: blended (xERA*0.5 + L3*0.5) * IP / 9
  - Direction accuracy (does it pick the right side of the line?)

Skip outs metric — known grading bug poisons the comparison.
"""
import os
import re
from collections import defaultdict
from datetime import date, timedelta
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

# Map prop -> projection key + metric
PROP_TO_METRIC = {
    'er_over': '_projected_er', 'er_under': '_projected_er',
    'ks_over': '_projected_ks', 'ks_under': '_projected_ks',
    'bb_over': '_projected_bb', 'bb_under': '_projected_bb',
    'ha_over': '_projected_hits', 'ha_under': '_projected_hits',
}

L3_RE = re.compile(r'L3\s+ERA\s+(\d+\.\d+)', re.IGNORECASE)
XERA_RE = re.compile(r'xERA\s+(\d+\.\d+)', re.IGNORECASE)


def pull():
    rows = []
    off = 0
    # Exclude outs from this audit — final_value broken
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_pipeline_props?result=in.(Win,Loss)'
            f'&final_value=not.is.null'
            f'&prop_type=in.(er_over,er_under,ks_over,ks_under,bb_over,bb_under,'
            f'ha_over,ha_under)'
            f'&select=game_date,player_name,prop_type,prop_line,conviction,tier,'
            f'final_value,signals'
            f'&limit=1000&offset={off}',
            headers=H, timeout=30,
        )
        chunk = r.json()
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


def extract_l3_era(sigs):
    if not isinstance(sigs, dict): return None
    for v in sigs.values():
        if isinstance(v, str):
            m = L3_RE.search(v)
            if m:
                try: return float(m.group(1))
                except: pass
    return None


def extract_xera(sigs):
    """xERA lives in 'xera_high'/'xera_avg'/'xera_low' string values."""
    if not isinstance(sigs, dict): return None
    for v in sigs.values():
        if isinstance(v, str):
            m = XERA_RE.search(v)
            if m:
                try: return float(m.group(1))
                except: pass
    # Some props have 'xera' as raw float
    if isinstance(sigs.get('xera'), (int, float)):
        return float(sigs['xera'])
    return None


def main():
    rows = pull()
    print(f'Pulled {len(rows)} graded props')

    records = []
    for p in rows:
        if not isinstance(p, dict): continue
        sigs = p.get('signals') or {}
        if not isinstance(sigs, dict): continue
        proj_key = PROP_TO_METRIC.get(p['prop_type'])
        if not proj_key: continue
        panel = sigs.get(proj_key)
        if panel is None: continue
        xera = extract_xera(sigs)
        l3 = extract_l3_era(sigs)
        try:
            panel = float(panel)
            actual = float(p['final_value'])
            line = float(p['prop_line'])
        except (ValueError, TypeError): continue
        if xera is None and l3 is None: continue

        # For ER math: blend xERA and L3 ERA, project over 4.5 IP
        # For Ks/BB/Hits we don't have a simple math model — skip or use rate
        metric = proj_key.replace('_projected_', '')
        math_proj = None
        if metric == 'er' and xera is not None and l3 is not None:
            try:
                xera = float(xera)
                math_proj = (xera * 0.5 + l3 * 0.5) * 4.5 / 9.0
            except (TypeError, ValueError):
                math_proj = None

        records.append({
            'date': p['game_date'],
            'pitcher': p['player_name'],
            'metric': metric,
            'line': line,
            'panel_proj': panel,
            'math_proj': math_proj,
            'actual': actual,
            'tier': p['tier'],
        })

    df = pd.DataFrame(records)
    print(f'{len(df)} valid records')
    print()

    # ER head-to-head
    er = df[df['metric'] == 'er'].dropna(subset=['math_proj']).copy()
    print(f'== ER: Panel vs Math projection ==')
    print(f'n = {len(er)}')
    if len(er) > 0:
        er['panel_err'] = (er['panel_proj'] - er['actual']).abs()
        er['math_err'] = (er['math_proj'] - er['actual']).abs()
        panel_mae = er['panel_err'].mean()
        math_mae = er['math_err'].mean()
        print(f'  Panel MAE:  {panel_mae:.2f}')
        print(f'  Math MAE:   {math_mae:.2f}')
        print(f'  Δ:          {math_mae - panel_mae:+.2f}  ({"PANEL wins" if panel_mae < math_mae else "MATH wins"})')

        # Direction accuracy
        er['panel_dir'] = er.apply(lambda r: 'OVER' if r['panel_proj'] > r['line'] else 'UNDER', axis=1)
        er['math_dir'] = er.apply(lambda r: 'OVER' if r['math_proj'] > r['line'] else 'UNDER', axis=1)
        er['actual_dir'] = er.apply(lambda r: 'OVER' if r['actual'] > r['line'] else ('UNDER' if r['actual'] < r['line'] else 'PUSH'), axis=1)
        ok = er[er['actual_dir'] != 'PUSH']
        panel_acc = (ok['panel_dir'] == ok['actual_dir']).mean() * 100
        math_acc = (ok['math_dir'] == ok['actual_dir']).mean() * 100
        print(f'  Panel direction: {(ok["panel_dir"] == ok["actual_dir"]).sum()}/{len(ok)} ({panel_acc:.0f}%)')
        print(f'  Math direction:  {(ok["math_dir"] == ok["actual_dir"]).sum()}/{len(ok)} ({math_acc:.0f}%)')

        # When they disagree, who's right?
        disagree = er[er['panel_dir'] != er['math_dir']]
        if len(disagree) > 0:
            panel_won = (disagree['panel_dir'] == disagree['actual_dir']).sum()
            math_won = (disagree['math_dir'] == disagree['actual_dir']).sum()
            print(f'\n  When Panel and Math DISAGREE (n={len(disagree)}):')
            print(f'    Panel right: {panel_won}/{len(disagree)} ({100*panel_won/len(disagree):.0f}%)')
            print(f'    Math right:  {math_won}/{len(disagree)} ({100*math_won/len(disagree):.0f}%)')

        # Ensemble? avg of both
        er['ens_proj'] = (er['panel_proj'] + er['math_proj']) / 2
        er['ens_err'] = (er['ens_proj'] - er['actual']).abs()
        er['ens_dir'] = er.apply(lambda r: 'OVER' if r['ens_proj'] > r['line'] else 'UNDER', axis=1)
        ok = er[er['actual_dir'] != 'PUSH']
        ens_acc = (ok['ens_dir'] == ok['actual_dir']).mean() * 100
        print(f'\n  ENSEMBLE (Panel + Math) /2:')
        print(f'    MAE: {er["ens_err"].mean():.2f}')
        print(f'    Direction: {(ok["ens_dir"] == ok["actual_dir"]).sum()}/{len(ok)} ({ens_acc:.0f}%)')


if __name__ == '__main__':
    main()
