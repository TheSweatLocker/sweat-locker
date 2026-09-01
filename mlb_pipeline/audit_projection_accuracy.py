"""Projection-vs-actual audit — the systematic RCA for prop scorer bias.

Motivation (2026-08-31 discussion): "Killing losing prop buckets is a
band-aid. The real question is why our scorer picks the wrong side."

For every graded pick in a date range, this tool:
  1. Extracts our PROJECTED VALUE from signals JSONB (_projected_ks,
     _projected_bb, _projected_er, etc.)
  2. Compares to the actual final_value the outcome landed at
  3. Computes systematic bias per prop_type (are we over/under
     projecting on average? by how much?)
  4. Cross-tabs signal presence on winners vs losers — signals that
     fire ONLY on losers are anti-signal (invert or drop weight)
  5. Detects direction errors — cases where our projected side lost
     but the opposite side would have won at exactly the same line

Outputs structured findings per prop_type × tier so downstream
fixes are surgical (specific bias corrections, specific signal
weight changes) rather than blanket kill lists.

Usage:
    python audit_projection_accuracy.py                  # last 30d
    python audit_projection_accuracy.py --days 60
    python audit_projection_accuracy.py --prop ks_under  # single type
    python audit_projection_accuracy.py --tier PRIME     # single tier
    python audit_projection_accuracy.py --md > audit.md  # markdown report
"""
import argparse
import os
import sys
import json
import statistics
from collections import defaultdict, Counter
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

# Which key in signals JSONB carries the projection per prop_type.
# Prefer refined (post-framing) over raw when available.
PROJECTION_KEY = {
    'ks_over': '_projected_ks', 'ks_under': '_projected_ks',
    'bb_over': '_projected_bb', 'bb_under': '_projected_bb',
    'ha_over': '_projected_hits', 'ha_under': '_projected_hits',
    'outs_over': '_projected_outs', 'outs_under': '_projected_outs',
    'er_over': '_projected_er', 'er_under': '_projected_er',
    # hits_over/hits_under are 0.5 line — projection is prob-based; skip
}


def _fetch_props(since_date: str, prop_filter: str | None, tier_filter: str | None) -> list:
    q = f'{SB}/rest/v1/mlb_pipeline_props?game_date=gte.{since_date}&result=in.(Win,Loss,Push)&select=game_date,player_name,prop_type,prop_line,direction,tier,conviction,book_over_odds,book_under_odds,result,final_value,signals&order=game_date.desc&limit=5000'
    if prop_filter:
        q += f'&prop_type=eq.{prop_filter}'
    if tier_filter:
        q += f'&tier=eq.{tier_filter}'
    r = requests.get(q, headers=H, timeout=30)
    return r.json() if r.status_code == 200 else []


def _extract_projection(row: dict) -> float | None:
    """Get projected numeric value from signals JSONB."""
    pt = row.get('prop_type')
    key = PROJECTION_KEY.get(pt)
    if not key: return None
    sig = row.get('signals') or {}
    if not isinstance(sig, dict): return None
    v = sig.get(key)
    if v is None: return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _implied_side(proj: float, line: float) -> str:
    return 'over' if proj > line else 'under' if proj < line else 'push'


def _payout(o):
    if o is None: return 0.91
    try:
        o = float(o)
        return o / 100 if o > 0 else 100 / abs(o)
    except (TypeError, ValueError):
        return 0.91


def analyze(rows: list) -> dict:
    """Run the full audit. Returns dict of findings per (prop_type, tier).

    Findings shape:
      { (prop_type, tier): {
          n, w, l, hit_pct, units,
          projection_bias: {mean_projected, mean_actual, bias, samples},
          direction_analysis: {picks_right_side, picks_wrong_side, would_flip_at_same_line},
          signal_correlation: [(signal_name, fires_on_wins, fires_on_losses, delta), ...],
      }}
    """
    by_bucket = defaultdict(list)
    for r in rows:
        pt = r.get('prop_type'); tier = r.get('tier')
        if tier not in ('PRIME', 'STRONG', 'LEAN'): continue
        by_bucket[(pt, tier)].append(r)

    findings = {}
    for (pt, tier), bucket in by_bucket.items():
        w = sum(1 for r in bucket if r['result'] == 'Win')
        l = sum(1 for r in bucket if r['result'] == 'Loss')
        p = sum(1 for r in bucket if r['result'] == 'Push')
        n = w + l
        if n < 5: continue  # too small to trust
        hit_pct = 100 * w / n

        # ─── PROJECTION BIAS ──────────────────────────────────────
        projected = []; actual = []
        for r in bucket:
            proj = _extract_projection(r)
            fv = r.get('final_value')
            if proj is None or fv is None: continue
            try: actual.append(float(fv))
            except: continue
            projected.append(proj)
        proj_bias = None
        if len(projected) >= 10:
            mean_proj = statistics.mean(projected)
            mean_act = statistics.mean(actual)
            proj_bias = {
                'n': len(projected),
                'mean_projected': round(mean_proj, 2),
                'mean_actual': round(mean_act, 2),
                'bias': round(mean_proj - mean_act, 2),  # + = we over-project
                'std_projected': round(statistics.pstdev(projected), 2),
                'std_actual': round(statistics.pstdev(actual), 2),
            }

        # ─── DIRECTION ANALYSIS ──────────────────────────────────
        # For each pick: was our direction right? (does actual clear line
        # in the direction we picked?)
        # + What would inverting the direction have done?
        dir_right = 0; dir_wrong = 0; would_flip_gain = 0.0; total_units = 0.0
        for r in bucket:
            fv = r.get('final_value')
            line = r.get('prop_line')
            direction = r.get('direction')
            if fv is None or line is None: continue
            try: fv_f = float(fv); line_f = float(line)
            except: continue
            actual_side = 'over' if fv_f > line_f else 'under' if fv_f < line_f else 'push'
            if actual_side == 'push': continue
            pick_correct = direction == actual_side
            if pick_correct: dir_right += 1
            else: dir_wrong += 1
            # Would inverting have won at same juice?
            odds = r.get('book_over_odds') if direction == 'over' else r.get('book_under_odds')
            inv_odds = r.get('book_under_odds') if direction == 'over' else r.get('book_over_odds')
            if pick_correct:
                total_units += _payout(odds)
            else:
                total_units -= 1
                # If we'd inverted, we'd have won at inv_odds
                would_flip_gain += _payout(inv_odds) + 1  # +payout instead of -1
        dir_analysis = {
            'picks_right_side': dir_right,
            'picks_wrong_side': dir_wrong,
            'right_pct': round(100 * dir_right / (dir_right + dir_wrong), 1) if (dir_right + dir_wrong) else None,
            'actual_units': round(total_units, 2),
            'if_inverted_units': round(total_units + would_flip_gain, 2),
            'flip_delta': round(would_flip_gain, 2),
        }

        # ─── SIGNAL CORRELATION ─────────────────────────────────
        # For each signal key that fires on 5+ picks in this bucket,
        # compute win-rate difference between signal-present and absent.
        sig_wins = defaultdict(int); sig_losses = defaultdict(int)
        no_sig_wins = 0; no_sig_losses = 0  # per pick baseline
        signal_keys_seen = set()
        for r in bucket:
            res = r.get('result')
            if res not in ('Win', 'Loss'): continue
            sig = r.get('signals') or {}
            if not isinstance(sig, dict): continue
            for k, v in sig.items():
                if k.startswith('_'): continue  # skip meta fields
                if v is None or v == '' or v == 0: continue
                signal_keys_seen.add(k)
                if res == 'Win': sig_wins[k] += 1
                else: sig_losses[k] += 1
        # Compute deltas: signals that fire more on losses than wins are anti-signal
        signal_corr = []
        for k in signal_keys_seen:
            w_ = sig_wins[k]; l_ = sig_losses[k]
            if w_ + l_ < 5: continue
            wr = w_ / (w_ + l_)
            signal_corr.append({
                'signal': k, 'wins': w_, 'losses': l_,
                'fire_win_rate': round(wr, 3),
                'edge_pp': round((wr - w / n) * 100, 1) if n else 0,
            })
        signal_corr.sort(key=lambda x: x['edge_pp'])

        findings[(pt, tier)] = {
            'n': n, 'w': w, 'l': l, 'p': p, 'hit_pct': round(hit_pct, 1),
            'projection_bias': proj_bias,
            'direction_analysis': dir_analysis,
            'signal_correlation': signal_corr,
        }
    return findings


def print_findings(findings: dict, md: bool = False):
    # Sort worst → best by units drag
    items = sorted(findings.items(),
                   key=lambda x: x[1]['direction_analysis']['actual_units'])
    for (pt, tier), f in items:
        hd = f'{pt} × {tier}'
        if md:
            print(f'\n## {hd}  ·  {f["w"]}-{f["l"]}-{f["p"]}  {f["hit_pct"]:.1f}%  {f["direction_analysis"]["actual_units"]:+.2f}u\n')
        else:
            print(f'\n════════════════════════════════════════════════════════════════')
            print(f'  {hd}  ·  {f["w"]}-{f["l"]}-{f["p"]} ({f["hit_pct"]:.1f}%)  units {f["direction_analysis"]["actual_units"]:+.2f}u')
            print(f'════════════════════════════════════════════════════════════════')

        pb = f['projection_bias']
        if pb:
            direction = 'OVER' if pb['bias'] > 0 else 'UNDER'
            severity = 'MAJOR' if abs(pb['bias']) > 0.5 else 'MINOR'
            print(f'  PROJECTION BIAS: projected {pb["mean_projected"]} vs actual {pb["mean_actual"]}  →  we systematically {direction}-project by {abs(pb["bias"]):.2f} (n={pb["n"]}, {severity})')
        else:
            print(f'  PROJECTION BIAS: no projection field for this prop type in signals JSONB')

        da = f['direction_analysis']
        print(f'  DIRECTION:  {da["picks_right_side"]}/{da["picks_right_side"]+da["picks_wrong_side"]} on right side of line  ({da["right_pct"]}%)')
        if da['flip_delta'] > 3:
            print(f'  🚨 IF INVERTED: {da["if_inverted_units"]:+.2f}u (gain of {da["flip_delta"]:+.2f}u) — direction may be systematically wrong')

        sc = f['signal_correlation']
        # Bottom 3 signals (anti-correlated)
        if sc:
            print(f'  ANTI-SIGNALS (fire more on losses):')
            for s in sc[:3]:
                if s['edge_pp'] < -5:
                    print(f'    · {s["signal"]:<25} fires {s["wins"]}W/{s["losses"]}L  ({s["fire_win_rate"]:.1%}, edge {s["edge_pp"]:+.1f}pp)')
            print(f'  PRO-SIGNALS (fire more on wins):')
            for s in sc[-3:]:
                if s['edge_pp'] > 5:
                    print(f'    · {s["signal"]:<25} fires {s["wins"]}W/{s["losses"]}L  ({s["fire_win_rate"]:.1%}, edge {s["edge_pp"]:+.1f}pp)')


def print_summary(findings: dict):
    """Aggregate roll-up + actionable fix list."""
    total_units = sum(f['direction_analysis']['actual_units'] for f in findings.values())
    would_be = sum(f['direction_analysis']['if_inverted_units'] for f in findings.values())

    print(f'\n═══════════════════════════════════════════════════════════════')
    print(f'  AUDIT SUMMARY')
    print(f'═══════════════════════════════════════════════════════════════')
    print(f'  {len(findings)} bucket(s) audited · total units {total_units:+.2f}u')
    print()
    print(f'  🎯 SYSTEMATIC FIXES QUEUED:')
    fixes = []
    for (pt, tier), f in findings.items():
        pb = f['projection_bias']
        da = f['direction_analysis']
        # Fix candidate: projection bias > 0.4
        if pb and abs(pb['bias']) >= 0.4:
            fix_dir = 'subtract' if pb['bias'] > 0 else 'add'
            fixes.append(f'   BIAS-FIX {pt:<12} {tier:<6}: {fix_dir} {abs(pb["bias"]):.2f} from _projected_{pt.split("_")[0]}  ({pb["n"]} samples)')
        # Fix candidate: direction inverted
        if da['flip_delta'] > 5 and da['right_pct'] and da['right_pct'] < 45:
            fixes.append(f'   FLIP-DIR {pt:<12} {tier:<6}: invert direction — {da["right_pct"]}% right side, +{da["flip_delta"]:.1f}u gain')
        # Fix candidate: kill anti-signals
        for s in f['signal_correlation'][:3]:
            if s['edge_pp'] < -10 and (s['wins'] + s['losses']) >= 10:
                fixes.append(f'   DROP-SIG {pt:<12} {tier:<6}: signal "{s["signal"]}" is anti-correlated ({s["edge_pp"]:+.1f}pp)')
    if not fixes:
        print(f'    (no high-confidence systematic fixes surfaced — buckets need larger sample)')
    for f in fixes[:20]:
        print(f)


def run(days: int, prop_filter: str | None, tier_filter: str | None, md: bool):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    print(f'=== Projection-vs-Actual Audit · since {since} ===')
    rows = _fetch_props(since, prop_filter, tier_filter)
    print(f'  {len(rows)} graded props loaded')
    findings = analyze(rows)
    print(f'  {len(findings)} (prop_type × tier) buckets with n≥5\n')
    print_findings(findings, md=md)
    print_summary(findings)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--prop', dest='prop_filter', default=None)
    ap.add_argument('--tier', dest='tier_filter', default=None)
    ap.add_argument('--md', action='store_true', help='markdown output')
    args = ap.parse_args()
    run(days=args.days, prop_filter=args.prop_filter, tier_filter=args.tier_filter, md=args.md)


if __name__ == '__main__':
    main()
