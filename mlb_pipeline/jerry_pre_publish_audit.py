"""Pre-publish sanity audit for Jerry reads (2026-08-10).

Runs AFTER all collapse scripts (contradictions, pitcher-thesis,
sharp-fade) and BEFORE generate_sweat_card. Scans today's slate for
known bug patterns that keep recurring across sessions:

  1. NULL call_text on non-pass reads
  2. "the opposing starter" / "opposing pitcher" leaks in prose
  3. Sharp-fade discipline violations that weren't auto-flipped
     (edge case where sharp-fade rules were bypassed)
  4. Duplicate-pitcher-name hallucinations ("Skubal has been sharp...
     but Skubal is getting rocked")
  5. Numeric integrity flags that should have been demoted but weren't

## Exit behavior

- FAIL LOUD (exit 1) when any critical bug found → GHA cron marks step failed
  → user gets notification, sweat card build is SKIPPED so bad reads don't
  reach users. This is the "cost of a bad read shipping > cost of a missed
  cron" tradeoff.
- Warnings-only (exit 0) for stylistic issues that don't block publication.

Sport-agnostic: audits MLB + UFC + any sport with jerry_reads. Defaults
to today.

## Idempotent + fast
Runs in ~2 seconds against a full slate (single DB fetch, no LLM calls).

CLI:
    python jerry_pre_publish_audit.py [--date YYYY-MM-DD] [--sport MLB]
    python jerry_pre_publish_audit.py --warn-only   # never exit 1
"""
from __future__ import annotations
import argparse, os, re, sys, json
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _parse_snap(s):
    if not s: return {}
    if isinstance(s, str):
        try: return json.loads(s)
        except: return {}
    return s


def _find_duplicate_pitcher_hallucination(text: str) -> str | None:
    """Detect the 'Skubal has been sharp... but Skubal is getting rocked'
    pattern where the SAME pitcher name is used to describe two different
    people. Only flag when the second reference has a CONTRARY descriptor
    (rocked/shelled/getting tagged while first was sharp/solid/locked)."""
    if not text: return None
    for m in re.finditer(
        r'\b([A-Z][a-z]{3,})\s+(has been (?:sharp|solid|locked|elite|dominant))',
        text
    ):
        name = m.group(1)
        after = text[m.end():m.end()+300]
        contra = re.search(
            rf'\b{re.escape(name)}\s+(is getting rocked|is getting shelled|'
            rf'is getting tagged|has been rough|is bleeding runs|is in a rough patch)',
            after
        )
        if contra:
            return f'{name} used for BOTH sharp AND rocked in same passage'
    return None


def audit(sport: str, game_date: str) -> dict:
    """Returns {critical: [...], warnings: [...], totals: {...}}."""
    r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
        params={'sport': f'eq.{sport}', 'game_date': f'eq.{game_date}',
                'select': 'id,game_id,call_market,call_side,call_line,'
                          'call_text,conviction,short_read,long_read'},
        timeout=15)
    reads = r.json() if r.status_code == 200 else []
    ctxs_r = requests.get(f'{SB}/rest/v1/mlb_game_context' if sport == 'MLB' else
                          f'{SB}/rest/v1/{sport.lower()}_game_context',
        headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'game_id,away_team,home_team,close_total,jerry_pred_total,'
                          'projected_total,mc_probabilities,oddscrowd_snapshot'},
        timeout=15)
    ctxs = ctxs_r.json() if ctxs_r.status_code == 200 else []
    ctx_by = {c['game_id']: c for c in ctxs}

    critical, warnings = [], []
    for r in reads:
        gid = r.get('game_id', '?')
        ctx = ctx_by.get(gid) or {}
        matchup = f'{ctx.get("away_team","?")[:10]}@{ctx.get("home_team","?")[:10]}'
        ct = r.get('call_text')
        mkt = (r.get('call_market') or '').lower()
        side = r.get('call_side')
        prose = ((r.get('short_read') or '') + ' ' + (r.get('long_read') or '')).strip()

        # 1. NULL call_text on non-pass read
        if not ct and mkt != 'pass':
            critical.append(f'{matchup} id={r["id"]}: NULL call_text on {mkt} {side} '
                            f'(app fallback would show "Pass" badge)')

        # 2. Opposing starter/pitcher leak
        if re.search(r'\b(?:the )?opposing (starter|pitcher)\b|\bopp starter\b|\bopp pitcher\b',
                     prose, re.IGNORECASE):
            critical.append(f'{matchup} id={r["id"]}: prose contains "opposing starter/pitcher" '
                            f'(Layer D scrub failed)')

        # 3. Duplicate pitcher hallucination
        dup = _find_duplicate_pitcher_hallucination(r.get('short_read') or '') or \
              _find_duplicate_pitcher_hallucination(r.get('long_read') or '')
        if dup:
            critical.append(f'{matchup} id={r["id"]}: duplicate pitcher hallucination: {dup}')

        # 4. Sharp-fade violation not auto-flipped (matches collapse_sharp_fade_violations rules)
        if mkt == 'total' and ctx and 'Auto-flipped 2026-08-10' not in (r.get('short_read') or ''):
            line = ctx.get('close_total')
            j_tot = ctx.get('jerry_pred_total')
            v3_tot = ctx.get('projected_total')
            mc_tot = (ctx.get('mc_probabilities') or {}).get('mc_mean_total')
            snap = _parse_snap(ctx.get('oddscrowd_snapshot'))
            seg = snap.get('total') or {}
            sharp_side = (seg.get('pick') or '').upper()
            sharp_money = seg.get('money') or 0
            if sharp_side and sharp_money >= 65 and sharp_side == (side or '').upper():
                other = 'UNDER' if sharp_side == 'OVER' else 'OVER'
                against = 0
                for pred in (j_tot, v3_tot, mc_tot):
                    if pred is None or line is None: continue
                    if (pred > line + 0.3 and other == 'OVER') or (pred < line - 0.3 and other == 'UNDER'):
                        against += 1
                if against >= 2:
                    critical.append(f'{matchup} id={r["id"]}: sharp-fade discipline violation '
                                    f'({side} · sharp {sharp_money}% same · {against} models against)')

        # 5. Numeric integrity flag orphan (footer present but conv not capped)
        conv = r.get('conviction') or 0
        if 'Numeric integrity flag' in (r.get('short_read') or '') and conv > 55:
            warnings.append(f'{matchup} id={r["id"]}: numeric-flag footer present but conv={conv} '
                            f'not capped (expected <=55)')

    return {
        'critical': critical,
        'warnings': warnings,
        'totals': {'reads': len(reads), 'critical': len(critical), 'warnings': len(warnings)},
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--sport', default='MLB', help='MLB / UFC / NFL / NCAAF')
    p.add_argument('--warn-only', action='store_true',
                   help='Never exit 1 — log only. Use for dev / manual audits.')
    args = p.parse_args()
    gd = args.date or _et_today()
    print(f'=== jerry_pre_publish_audit · {args.sport} · {gd} ===')
    report = audit(args.sport, gd)
    t = report['totals']
    print(f'  scanned {t["reads"]} reads · {t["critical"]} critical · {t["warnings"]} warnings')
    if report['warnings']:
        print('\n  WARNINGS (non-blocking):')
        for w in report['warnings']:
            print(f'    - {w}')
    if report['critical']:
        print('\n  CRITICAL (block publication):')
        for c in report['critical']:
            print(f'    - {c}')
        if not args.warn_only:
            print('\n  Exiting 1 — sweat card build should NOT proceed with these '
                  'reads in the DB. Fix or wait for next Jerry regen.')
            sys.exit(1)
    else:
        print('  ok — no critical issues')


if __name__ == '__main__':
    main()
