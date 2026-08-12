"""Pre-publish sanity audit for Jerry reads (2026-08-10).

Runs AFTER all collapse scripts (contradictions, pitcher-thesis,
sharp-fade, refit-override) and BEFORE generate_sweat_card. Scans
today's slate for known bug patterns that keep recurring across sessions:

  1. NULL call_text on non-pass reads
  2. "the opposing starter" / "opposing pitcher" leaks in prose
  3. Sharp-fade discipline violations that weren't auto-flipped
     (edge case where sharp-fade rules were bypassed)
  4. Duplicate-pitcher-name hallucinations ("Skubal has been sharp...
     but Skubal is getting rocked")
  5. Numeric integrity flags that should have been demoted but weren't
  6. [2026-08-10] Refit coverage <95% on BACK/FADE Prop Jerry picks
  7. [2026-08-10] Refit weights version >14 days stale
  8. [2026-08-10] BACK verdict with refit <=40 (surviving trap)
  9. [2026-08-10] FADE verdict with refit >=65 (fighting refit signal)

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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


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

        # 2. Opposing starter/pitcher leak.
        # 2026-08-11: allow "(TBD)" parenthetical — that's the HONEST fallback
        # when a starter hasn't been announced yet. Only critical when the
        # phrase appears WITHOUT a parenthetical name/TBD marker.
        for m in re.finditer(
            r'\b(?:the )?opposing (?:starter|pitcher)\b|\bopp (?:starter|pitcher)\b',
            prose, re.IGNORECASE):
            after = prose[m.end():m.end()+15]
            if re.match(r'\s*\((?:TBD|opener)', after, re.IGNORECASE):
                continue  # legitimate TBD/opener fallback
            critical.append(f'{matchup} id={r["id"]}: prose contains "opposing starter/pitcher" '
                            f'(Layer D scrub failed)')
            break  # one report per read

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

        # 10. Stat-verifier cross-check against real MLB API (2026-08-10).
        # Catches the class of bugs from today: "Cameron 4.41 xERA getting
        # rocked" when L3 is 0.39, "Scott 1.3 IP opener" when he's a
        # 5-IP starter, "Wesneski 8.83 ERA" when L3 is 4.02.
        try:
            from jerry_stat_verifier import verify as stat_verify
            prose = (r.get('short_read') or '') + '\n' + (r.get('long_read') or '')
            for pkey in ('home_pitcher', 'away_pitcher'):
                pname = ctx.get(pkey)
                if not pname or pname == '(TBD)': continue
                sv = stat_verify(prose, pname)
                for v in sv.get('violations', []):
                    line = (f'{matchup} id={r["id"]} {pname}: '
                            f'{v["stat_key"]}={v["cited"]} but real is {v["expected"]} '
                            f'({v.get("reason","numeric drift")})')
                    if v['severity'] == 'critical':
                        critical.append(f'STAT_HALLUCINATION · {line}')
                    else:
                        warnings.append(f'stat_drift · {line}')
        except Exception as e:
            warnings.append(f'stat_verifier failed on id={r["id"]}: {e}')

    return {
        'critical': critical,
        'warnings': warnings,
        'totals': {'reads': len(reads), 'critical': len(critical), 'warnings': len(warnings)},
    }


def audit_prop_jerry_refit(game_date: str) -> dict:
    """2026-08-10: 4 refit-focused sanity gates on prop_jerry_reads + weights.

    Gates:
      6. Refit coverage <95% on BACK/FADE picks (silent apply_prop_refit failure)
      7. Refit weights file trained_at >14 days ago (stale calibration)
      8. Any BACK verdict where refit_conviction <=40 (surviving trap)
      9. Any FADE verdict where refit_conviction >=65 (fighting refit)

    Returns same {critical, warnings} shape as audit()."""
    critical, warnings = [], []

    # --- Gate 6 + 8 + 9: check today's prop_jerry_reads state ---
    reads = requests.get(f'{SB}/rest/v1/prop_jerry_reads', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'id,player_name,prop_type,direction,call_verdict,conviction'},
        timeout=15).json()
    if isinstance(reads, list):
        directional = [r for r in reads if (r.get('call_verdict') or '').upper() in ('BACK', 'FADE')]
        # Refit_conviction lives on the source prop row, not jerry row — cross-join
        props = requests.get(f'{SB}/rest/v1/mlb_pipeline_props', headers=H_READ,
            params={'game_date': f'eq.{game_date}',
                    'select': 'player_name,prop_type,prop_line,direction,refit_conviction'},
            timeout=15).json()
        prop_by_key = {(p['player_name'], p['prop_type'], p['direction']): p
                       for p in (props if isinstance(props, list) else [])}
        # 2026-08-10: count only jerry reads whose underlying prop row EXISTS
        # in the props table. Prop Jerry sometimes generates BOTH directions
        # (BACK on over, FADE on under) but mlb_pipeline_props may only carry
        # one direction. Not-found reads aren't a refit-coverage failure.
        matched = 0
        with_refit = 0
        for r in directional:
            key = (r['player_name'], r['prop_type'], r['direction'])
            prop = prop_by_key.get(key)
            if not prop: continue
            matched += 1
            refit = prop.get('refit_conviction')
            if refit is not None:
                with_refit += 1
                verdict = r['call_verdict'].upper()
                # Gate 8: BACK on refit trap.
                # 2026-08-11 (v2): sync with tightened REFIT_TRAP=30 in
                # apply_refit_verdict_override. Critical only when refit
                # is truly hammered (<=30). 30-40 band is warning territory
                # since override no longer force-flips there.
                if verdict == 'BACK' and refit <= 30:
                    critical.append(f'prop_jerry id={r["id"]} {r["player_name"]} {r["prop_type"]} '
                                    f'{r["direction"]}: BACK on refit={refit} (trap zone, expected FADE '
                                    f'via override — did apply_refit_verdict_override run?)')
                elif verdict == 'BACK' and refit <= 40:
                    warnings.append(f'prop_jerry id={r["id"]} {r["player_name"]} {r["prop_type"]} '
                                    f'{r["direction"]}: BACK with refit={refit} (mild trap zone, '
                                    f'gray-zone — Jerry may have counter-signal)')
                # Gate 9: FADE on strong refit.
                # 2026-08-11: split into two tiers.
                #   refit >= 80 → CRITICAL (override MUST flip; something broke)
                #   refit 65-79 → WARNING (override doesn't force flip in this
                #                          band; Jerry may have real counter-signal)
                # Prior single-threshold at 65 blocked entire cron on 12 gray-
                # zone FADEs today — false positive parity with the override
                # rules that only force flip at >= 80.
                if verdict == 'FADE' and refit >= 80:
                    critical.append(f'prop_jerry id={r["id"]} {r["player_name"]} {r["prop_type"]} '
                                    f'{r["direction"]}: FADE with refit={refit} (fighting strong refit '
                                    f'signal — override should have flipped to BACK)')
                elif verdict == 'FADE' and refit >= 65:
                    warnings.append(f'prop_jerry id={r["id"]} {r["player_name"]} {r["prop_type"]} '
                                    f'{r["direction"]}: FADE with refit={refit} (gray-zone, override '
                                    f'threshold is 80 — Jerry may have counter-signal)')
        # Gate 6: coverage (denominator = matched, not all directional)
        if matched:
            coverage = with_refit / matched
            if coverage < 0.95:
                critical.append(f'refit coverage {coverage*100:.0f}% ({with_refit}/{matched} '
                                f'jerry reads with matching prop row) — apply_prop_refit likely '
                                f'failed this cycle. Rerun `python apply_prop_refit.py`.')

    # --- Gate 11: L5 trend gate (2026-08-11 · Christian Scott motivator) ---
    # For each BACK prop_jerry pick on a pitcher metric (hits/er/ks/bb/ip),
    # check whether the pitcher's L5 trend for that metric contradicts the
    # pick direction. Warns/critical on contradiction; downstream sizing
    # should soften picks flagged here.
    try:
        from pitcher_trend_gate import check_trend
        pitcher_prop_types = {
            'ha_under': ('hits_allowed', 'under'),
            'ha_over': ('hits_allowed', 'over'),
            'er_under': ('er', 'under'),
            'er_over': ('er', 'over'),
            'ks_under': ('ks', 'under'),
            'ks_over': ('ks', 'over'),
            'bb_under': ('bb', 'under'),
            'bb_over': ('bb', 'over'),
            'outs_under': ('ip', 'under'),
            'outs_over': ('ip', 'over'),
        }
        pj_back = requests.get(f'{SB}/rest/v1/prop_jerry_reads', headers=H_READ,
            params={'game_date': f'eq.{game_date}',
                    'call_verdict': 'eq.BACK',
                    'conviction': 'gte.60',
                    'select': 'id,player_name,prop_type,direction,conviction'},
            timeout=15).json()
        if isinstance(pj_back, list):
            for pj in pj_back:
                pt = pj.get('prop_type'); dir_ = pj.get('direction')
                if pt not in pitcher_prop_types: continue
                metric, expected_dir = pitcher_prop_types[pt]
                if dir_ != expected_dir: continue
                tr = check_trend(pj['player_name'], metric, expected_dir)
                if tr['contradicts_pick']:
                    line = (f'prop_jerry id={pj["id"]} {pj["player_name"]} {pt} '
                            f'{dir_}: L5 trend contradicts BACK — {tr["note"]}')
                    if tr['severity'] == 'critical':
                        critical.append(f'TREND_CONTRADICTS · {line}')
                    else:
                        warnings.append(f'trend_watch · {line}')
    except ImportError:
        pass
    except Exception as e:
        warnings.append(f'trend gate failed: {e}')

    # --- Gate 7: weights file staleness ---
    from pathlib import Path
    from datetime import datetime, timedelta, timezone
    wpath = Path(__file__).parent / 'models' / 'prop_refit_weights_v2.json'
    if wpath.exists():
        try:
            data = json.loads(wpath.read_text())
            trained = data.get('trained_at')
            if trained:
                td = datetime.fromisoformat(trained.replace('Z', '+00:00'))
                if td.tzinfo is None:
                    td = td.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - td).days
                if age_days > 14:
                    critical.append(f'refit weights are {age_days} days stale (trained {trained[:10]}). '
                                    f'Weekly Sunday retrain cron missed — rerun '
                                    f'`python _fit_prop_refit_weights_v2.py`.')
                elif age_days > 10:
                    warnings.append(f'refit weights aging: {age_days}d since trained_at')
        except Exception as e:
            warnings.append(f'could not parse refit weights: {e}')
    else:
        critical.append('refit weights file missing: models/prop_refit_weights_v2.json')

    return {'critical': critical, 'warnings': warnings}


def auto_repair(sport: str, game_date: str) -> dict:
    """2026-08-11: comprehensive auto-repair layer. Runs BEFORE the fail check
    so pipeline is self-healing for every known critical class.

    Repairs applied:
      A. Layer D re-scrub on ALL jerry_reads + prop_jerry_reads prose. Any
         "opposing starter/pitcher" or "the [team] starter" leaks get
         mechanically substituted with actual pitcher names.
      B. L5 trend contradict → force PASS on any BACK pitcher prop where
         pitcher_trend_gate.check_trend returns contradicts_pick=True with
         critical severity.
      C. Duplicate-pitcher hallucination cases could be added here later;
         currently reported but not auto-repaired (would require regen).

    Returns {'A_scrubbed': N, 'B_trend_pass': N, 'total_repairs': N}.
    Sport-universal but MLB-first (only MLB has jerry_reads + prop_jerry_reads
    + pitcher_trend_gate today).
    """
    repairs = {'A_layer_d_jerry_reads': 0, 'A_layer_d_prop_jerry': 0,
               'B_trend_forced_pass': 0}

    # --- A. Layer D re-scrub jerry_reads ---
    try:
        from validate_jerry_read import substitute_generic_pitcher_refs
    except ImportError:
        substitute_generic_pitcher_refs = None

    if substitute_generic_pitcher_refs:
        # Fetch game_context for name resolution
        sport_ctx = {'MLB': ('mlb_game_context', 'home_pitcher', 'away_pitcher'),
                     'NFL': ('nfl_game_context', 'home_qb', 'away_qb'),
                     'NCAAF': ('ncaaf_game_context', 'home_qb', 'away_qb')}
        cfg = sport_ctx.get(sport)
        if cfg:
            ctx_table, home_key, away_key = cfg
            ctx_rows = requests.get(f'{SB}/rest/v1/{ctx_table}',
                headers=H_READ,
                params={'game_date': f'eq.{game_date}',
                        'select': f'game_id,home_team,away_team,{home_key},{away_key}'},
                timeout=15).json()
            ctx_by_gid = {c['game_id']: c for c in (ctx_rows if isinstance(ctx_rows, list) else [])}

            # jerry_reads sweep
            reads = requests.get(f'{SB}/rest/v1/jerry_reads',
                headers=H_READ,
                params={'game_date': f'eq.{game_date}',
                        'sport': f'eq.{sport}',
                        'select': 'id,game_id,short_read,long_read'},
                timeout=15).json()
            for r in (reads if isinstance(reads, list) else []):
                ctx = ctx_by_gid.get(r.get('game_id'))
                if not ctx: continue
                struct = {'home_team': ctx.get('home_team'),
                          'away_team': ctx.get('away_team'),
                          'home_pitcher': ctx.get(home_key),
                          'away_pitcher': ctx.get(away_key)}
                orig_short = r.get('short_read') or ''
                orig_long = r.get('long_read') or ''
                new_short = substitute_generic_pitcher_refs(orig_short, struct)
                new_long = substitute_generic_pitcher_refs(orig_long, struct)
                if new_short != orig_short or new_long != orig_long:
                    payload = {}
                    if new_short != orig_short: payload['short_read'] = new_short[:2000]
                    if new_long != orig_long: payload['long_read'] = new_long[:8000]
                    if payload:
                        pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{r["id"]}',
                                            headers=H_WRITE, json=payload, timeout=10)
                        if pr.status_code in (200, 204):
                            repairs['A_layer_d_jerry_reads'] += 1

            # prop_jerry_reads sweep (only if MLB — column has short_read only)
            if sport == 'MLB':
                pj_reads = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
                    headers=H_READ,
                    params={'game_date': f'eq.{game_date}',
                            'select': 'id,game_id,short_read'},
                    timeout=15).json()
                for r in (pj_reads if isinstance(pj_reads, list) else []):
                    ctx = ctx_by_gid.get(r.get('game_id'))
                    if not ctx: continue
                    struct = {'home_team': ctx.get('home_team'),
                              'away_team': ctx.get('away_team'),
                              'home_pitcher': ctx.get(home_key),
                              'away_pitcher': ctx.get(away_key)}
                    orig = r.get('short_read') or ''
                    scrubbed = substitute_generic_pitcher_refs(orig, struct)
                    if scrubbed != orig:
                        pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{r["id"]}',
                                            headers=H_WRITE,
                                            json={'short_read': scrubbed[:1500]}, timeout=10)
                        if pr.status_code in (200, 204):
                            repairs['A_layer_d_prop_jerry'] += 1

    # --- B. L5 trend contradict → force PASS (MLB only) ---
    if sport == 'MLB':
        try:
            from pitcher_trend_gate import check_trend
        except ImportError:
            check_trend = None
        if check_trend:
            pitcher_prop_types = {
                'ha_under': ('hits_allowed', 'under'), 'ha_over': ('hits_allowed', 'over'),
                'er_under': ('er', 'under'), 'er_over': ('er', 'over'),
                'ks_under': ('ks', 'under'), 'ks_over': ('ks', 'over'),
                'bb_under': ('bb', 'under'), 'bb_over': ('bb', 'over'),
                'outs_under': ('ip', 'under'), 'outs_over': ('ip', 'over'),
            }
            pj_back = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
                headers=H_READ,
                params={'game_date': f'eq.{game_date}',
                        'call_verdict': 'eq.BACK',
                        'conviction': 'gte.55',
                        'select': 'id,player_name,prop_type,direction,conviction,short_read'},
                timeout=15).json()
            for pj in (pj_back if isinstance(pj_back, list) else []):
                pt = pj.get('prop_type'); dir_ = pj.get('direction')
                if pt not in pitcher_prop_types: continue
                metric, expected_dir = pitcher_prop_types[pt]
                if dir_ != expected_dir: continue
                try:
                    tr = check_trend(pj['player_name'], metric, expected_dir)
                except Exception:
                    continue
                if tr.get('contradicts_pick') and tr.get('severity') == 'critical':
                    note = (f'[Auto-trend-repair 2026-08-11 TREND_CONTRADICTS_CRITICAL: '
                            f'{tr["note"]} — forcing PASS. Original take: '
                            f'{(pj.get("short_read") or "")[:220]}]')
                    payload = {'call_verdict': 'PASS', 'conviction': 40,
                               'short_read': note[:1500]}
                    pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{pj["id"]}',
                                        headers=H_WRITE, json=payload, timeout=10)
                    if pr.status_code in (200, 204):
                        repairs['B_trend_forced_pass'] += 1

    # --- C + D. Refit-drift correction using PROPS-TABLE refit (source of truth) ---
    # 2026-08-12 (v2): prop_jerry_reads.refit_conviction is stamped once at
    # synthesis time and never re-refit — it goes stale within the same day
    # if apply_prop_refit is re-run. Props table is the source of truth. My
    # earlier v1 read refit from JR and made wrong decisions (converted a
    # legitimate BACK to FADE because JR had stale refit=0 while props had
    # refit=100). Now we cross-join to props table.
    #
    # Two classes handled together (single props fetch):
    #   C. BACK verdict + props.refit <= 30 → force FADE (trap zone)
    #   D. FADE verdict + props.refit >= 80 → force BACK LEAN cap (Jerry misfire)
    if sport == 'MLB':
        repairs['C_trap_forced_fade'] = 0
        repairs['D_flip_fade_to_back'] = 0
        # All BACK or FADE JR rows for the day
        pj_all = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
            headers=H_READ,
            params={'game_date': f'eq.{game_date}',
                    'call_verdict': 'in.(BACK,FADE)',
                    'select': 'id,player_name,prop_type,prop_line,direction,'
                              'call_verdict,conviction,short_read'},
            timeout=15).json()
        # All refit-populated props for the day
        props_all = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
            headers=H_READ,
            params={'game_date': f'eq.{game_date}',
                    'refit_conviction': 'not.is.null',
                    'select': 'player_name,prop_type,prop_line,direction,refit_conviction'},
            timeout=15).json()
        # Build lookup with (player, prop_type, prop_line, direction) key
        prop_lookup = {(p['player_name'], p['prop_type'], p['prop_line'], p['direction']): p
                       for p in (props_all if isinstance(props_all, list) else [])}

        def _find_fresh_refit(pj):
            """Exact key match, fall back to nearest-line on (player, prop_type, direction)."""
            key = (pj['player_name'], pj['prop_type'], pj['prop_line'], pj['direction'])
            p = prop_lookup.get(key)
            if p: return p.get('refit_conviction')
            # Fallback: nearest line on same (player, prop_type, direction)
            candidates = [pp for pp in (props_all if isinstance(props_all, list) else [])
                          if pp.get('player_name') == pj['player_name']
                          and pp.get('prop_type') == pj['prop_type']
                          and pp.get('direction') == pj['direction']]
            if not candidates: return None
            try:
                jr_line = float(pj['prop_line']) if pj.get('prop_line') is not None else None
                if jr_line is not None:
                    candidates.sort(key=lambda p:
                        abs(float(p['prop_line']) - jr_line) if p.get('prop_line') is not None else 999)
            except (TypeError, ValueError): pass
            return candidates[0].get('refit_conviction')

        for pj in (pj_all if isinstance(pj_all, list) else []):
            verdict = (pj.get('call_verdict') or '').upper()
            fresh_refit = _find_fresh_refit(pj)
            if fresh_refit is None: continue
            # Class C: BACK on trap zone (refit <= 30) → FADE
            if verdict == 'BACK' and fresh_refit <= 30:
                note = (f'[Auto-trap-repair 2026-08-12 REFIT_TRAP_FORCE_FADE: '
                        f'fresh_refit={fresh_refit} in trap zone (<= 30) '
                        f'but BACK verdict slipped past override. Forcing '
                        f'FADE. Original take: {(pj.get("short_read") or "")[:200]}]')
                payload = {'call_verdict': 'FADE', 'conviction': 55,
                           'short_read': note[:1500]}
                pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{pj["id"]}',
                                    headers=H_WRITE, json=payload, timeout=10)
                if pr.status_code in (200, 204):
                    repairs['C_trap_forced_fade'] += 1
            # Class D: FADE on strong refit (>= 80) → BACK LEAN cap
            elif verdict == 'FADE' and fresh_refit >= 80:
                note = (f'[Auto-flip-repair 2026-08-12 REFIT_FLIP_TO_BACK: '
                        f'fresh_refit={fresh_refit} says LOAD but Jerry FADEd '
                        f'— override missed. Flipping FADE→BACK at LEAN cap. '
                        f'Original take: {(pj.get("short_read") or "")[:200]}]')
                payload = {'call_verdict': 'BACK', 'conviction': 55,
                           'short_read': note[:1500]}
                pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{pj["id"]}',
                                    headers=H_WRITE, json=payload, timeout=10)
                if pr.status_code in (200, 204):
                    repairs['D_flip_fade_to_back'] += 1

    repairs['total_repairs'] = sum(v for k, v in repairs.items() if k != 'total_repairs')
    return repairs


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--sport', default='MLB', help='MLB / UFC / NFL / NCAAF')
    p.add_argument('--warn-only', action='store_true',
                   help='Never exit 1 — log only. Use for dev / manual audits.')
    p.add_argument('--repair', action='store_true',
                   help='Auto-repair known critical classes (Layer D scrub + '
                        'trend-contradict PASS) BEFORE the fail check. Cron '
                        'should always pass this so pipeline is self-healing.')
    args = p.parse_args()
    gd = args.date or _et_today()
    print(f'=== jerry_pre_publish_audit · {args.sport} · {gd} ===')

    # 2026-08-11: --repair mode auto-fixes every known critical class before
    # the fail check runs. Cron passes --repair so pipeline is self-healing.
    # Pattern classes covered:
    #   * Layer D scrub on all game reads + prop jerry reads
    #   * L5 trend-contradict → force PASS on BACK pitcher props (critical only)
    if args.repair:
        repairs = auto_repair(args.sport, gd)
        if repairs['total_repairs'] > 0:
            print(f'  🔧 auto-repair applied: {repairs}')
        else:
            print(f'  🔧 auto-repair: no fixes needed')

    report = audit(args.sport, gd)

    # 2026-08-10: also run refit-focused gates on Prop Jerry (MLB only for now
    # since refit v2 is MLB-only until we ship refit for NFL props).
    if args.sport == 'MLB':
        refit_report = audit_prop_jerry_refit(gd)
        report['critical'].extend(refit_report['critical'])
        report['warnings'].extend(refit_report['warnings'])
        report['totals']['critical'] = len(report['critical'])
        report['totals']['warnings'] = len(report['warnings'])

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
