"""Jerry-anchored POTD (2026-07-31 · Sweat Card swap).

Runs AFTER generate_jerry_synthesis.py in the cron chain. Reads jerry_reads
for today, picks the highest-conviction call (>= threshold), and overwrites
best_bet_YYYY-MM-DD in jerry_cache with the Jerry-anchored payload.

Keeps the SAME jerry_cache schema so downstream (generate_tonight_card.py,
Sweat Card app render) continue reading without change — just now the pick
selection is based on jerry_reads.conviction instead of primary_play.tier
+ confluence composite.

Threshold policy (2026-07-31 user decision):
  - conviction >= 70 → eligible for POTD (POTD = MAX conviction across slate)
  - conviction 60-69 → no POTD (Jerry passes on the day, discipline preserved)
  - MARKET=pass rows never eligible

Sport support: MLB only for v1. NBA/NFL/NCAAF/NCAAB add when their Jerry
synthesizers ship.

Usage:
    python jerry_anchor_potd.py [--date YYYY-MM-DD] [--threshold 70] [--dry-run]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass

H_READ = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
H_WRITE = {**H_READ, "Content-Type": "application/json",
           "Prefer": "resolution=merge-duplicates,return=minimal"}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


# Sport → game context table registry (2026-08-03 sport-universalization).
# Add rows here as each sport's game_context table ships. Fallback path
# handles POTD winners from sports without a context table (best-effort;
# skips juice gate + context-details fetch, but still writes the POTD).
CONTEXT_TABLE_BY_SPORT = {
    "MLB": "mlb_game_context",
    "NFL": "nfl_game_context",        # migration applied 2026-08-03
    # "NBA": "nba_game_context",
    # "NCAAF": "ncaaf_game_context",
    # "NCAAB": "ncaab_game_context",
    # "UFC": "ufc_fight_context",
}


def _context_table(sport: str) -> Optional[str]:
    return CONTEXT_TABLE_BY_SPORT.get((sport or "").upper())


def _conviction_tier(conv: int) -> str:
    """Match downstream label shape (line 140 of generate_tonight_card.py:
       'Tier: {confidence.upper()} | Jerry {score}/100')."""
    if conv >= 80: return "prime"
    if conv >= 70: return "strong"
    if conv >= 60: return "solid"
    return "lean"


# 2026-09-09 POTD SOURCE EXPANSION — user directive from surface walkthrough.
# Prior behavior: only considered jerry_reads (LLM narrative conviction,
# capped 40-68 many days). Missed PRIMARY_PLAY (real resolved pick from
# ensemble + LR override, conv 76+) AND all props entirely — POTD couldn't
# select a prop even if it was the top-conviction play on the slate.
# New: pool candidates from THREE sources per day, apply same threshold +
# LR + juice gates on the merged set.
PROP_TABLE_BY_SPORT = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',
}


def _load_primary_play_candidates(gd: str, sports: list) -> list:
    """Return primary_play from each sport's game_context as pseudo-jerry_reads.

    Emits candidates with fields matching jerry_reads schema (sport, game_id,
    call_market, call_side, call_line, conviction, short_read, call_text)
    so the downstream threshold + gate pipeline can process them uniformly.
    Only surfaces PRIME + STRONG plays; LEAN not POTD-worthy.

    2026-09-10 KICKOFF FILTER: excludes games that have already started —
    prevents POTD from picking a game played last night when its UTC date
    happens to be today. NE @ SEA TNF 9/9 8:20 PM ET was UTC 00:20 9/10
    → had game_date 2026-09-10 → became "today" POTD candidate → picked
    9/10 morning even though game was already final. Now we require
    kickoff to be in the future (grace: within 15 min of now to allow
    small clock skew, but no games already played).
    """
    from datetime import datetime as _dt, timezone as _tz
    now_utc = _dt.now(_tz.utc)
    out = []
    for sport in sports:
        ctx_table = CONTEXT_TABLE_BY_SPORT.get(sport)
        if not ctx_table: continue
        try:
            # 2026-09-11: mlb_game_context has NO commence_time column
            # (schema stores just game_date). Selecting it caused ~thousands
            # of 42703 errors/day in Supabase logs — every POTD compute pass.
            # Fix: MLB skips the kickoff filter (game_date match is enough
            # for MLB slates that are all today anyway); NFL/NCAAF use
            # kickoff_utc for cross-day/night-game filtering.
            kickoff_col = 'kickoff_utc' if sport in ('NFL', 'NCAAF') else None
            select = (f"game_id,primary_play,home_team,away_team,{kickoff_col}"
                      if kickoff_col else "game_id,primary_play,home_team,away_team")
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/{ctx_table}",
                headers=H_READ,
                params={"game_date": f"eq.{gd}",
                        "primary_play": "not.is.null",
                        "select": select},
                timeout=15,
            )
            rows = r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f"  ⚠ {sport} primary_play load failed: {e}")
            continue
        skipped_past = 0
        for row in rows:
            # 2026-09-10 KICKOFF FILTER — skip games already started/played
            kickoff_str = row.get('kickoff_utc') or row.get('commence_time')
            if kickoff_str:
                try:
                    ko = _dt.fromisoformat(str(kickoff_str).replace('Z','+00:00'))
                    if ko.tzinfo is None: ko = ko.replace(tzinfo=_tz.utc)
                    # Grace: 15-min slack for late lock windows
                    from datetime import timedelta as _td
                    if ko < now_utc - _td(minutes=15):
                        skipped_past += 1
                        continue
                except Exception: pass  # bad timestamp — allow through
            pp = row.get('primary_play') or {}
            if isinstance(pp, str):
                try: pp = json.loads(pp)
                except Exception: pp = {}
            if not isinstance(pp, dict): continue
            tier = str(pp.get('tier') or '').upper()
            if tier not in ('PRIME', 'STRONG'): continue
            conv = int(pp.get('conviction') or 0)
            side = str(pp.get('side') or '').upper()
            market = str(pp.get('type') or pp.get('market') or '').lower()
            label = pp.get('label') or ''
            out.append({
                'sport': sport,
                'game_id': row['game_id'],
                'call_market': market,
                'call_side': side,
                'call_line': pp.get('line'),
                'conviction': conv,
                'call_text': label,
                'short_read': pp.get('sub') or pp.get('audit_note') or '',
                'long_read': '',
                'generated_at': None,
                '_source': 'primary_play',
            })
        if skipped_past:
            print(f"  ⏭  {sport} primary_play: skipped {skipped_past} games with kickoff in past")
    return out


def _load_top_prop_candidates(gd: str, min_conv: int = 80) -> list:
    """Return top PRIME props (MLB + NFL) as POTD candidates.

    Props are typically higher-variance than sides/totals, so require a
    higher conviction floor (default 80) to be POTD-eligible. Same LR gate
    later — props without _lr_ml_shadow pass through, but the refit_conviction
    field acts as a proxy quality check.

    2026-09-10 KICKOFF FILTER: join with game_context to get kickoff time
    per game, exclude props for games already played. Prevents NFL props
    like "Arroyo receptions Under 1.5 conv 95" from being picked as POTD
    the morning AFTER TNF because prop game_date=2026-09-10 (UTC).
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    now_utc = _dt.now(_tz.utc)

    # Preload kickoff times for today's games across all sports we care about.
    # 2026-09-11 same fix as above — MLB has no commence_time column, only NFL/NCAAF
    # ctx tables carry kickoff_utc. For MLB, kickoff_by_gid stays empty (all today).
    kickoff_by_gid = {}
    for sport, ctx_table in CONTEXT_TABLE_BY_SPORT.items():
        if not ctx_table: continue
        if sport not in ('NFL', 'NCAAF'):
            continue  # only weekly sports need kickoff-time filter
        try:
            kc = 'kickoff_utc'
            rr = requests.get(f"{SUPABASE_URL}/rest/v1/{ctx_table}", headers=H_READ,
                params={'game_date': f'eq.{gd}', 'select': f'game_id,{kc}'}, timeout=10)
            for row in (rr.json() if rr.status_code == 200 else []):
                if isinstance(row, dict) and row.get('game_id'):
                    kickoff_by_gid[row['game_id']] = row.get('kickoff_utc') or row.get('commence_time')
        except Exception: continue

    out = []
    for sport, tbl in PROP_TABLE_BY_SPORT.items():
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/{tbl}",
                headers=H_READ,
                params={"game_date": f"eq.{gd}",
                        "tier": "eq.PRIME",
                        "select": "game_id,player_name,prop_type,direction,prop_line,"
                                  "book_over_odds,book_under_odds,conviction,refit_conviction,matchup",
                        "order": "conviction.desc",
                        "limit": "10"},
                timeout=15,
            )
            rows = r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f"  ⚠ {sport} prop candidates load failed: {e}")
            continue
        skipped_past = 0
        for row in rows:
            # 2026-09-10 KICKOFF FILTER — same as primary_play loader
            kickoff_str = kickoff_by_gid.get(row.get('game_id'))
            if kickoff_str:
                try:
                    ko = _dt.fromisoformat(str(kickoff_str).replace('Z','+00:00'))
                    if ko.tzinfo is None: ko = ko.replace(tzinfo=_tz.utc)
                    if ko < now_utc - _td(minutes=15):
                        skipped_past += 1
                        continue
                except Exception: pass
            conv = int(row.get('conviction') or 0)
            refit = row.get('refit_conviction')
            # Prefer refit when present — it's the calibrated probability
            effective_conv = int(refit) if refit is not None else conv
            if effective_conv < min_conv: continue
            # 2026-09-11 KILL hits_over from POTD pool per user directive.
            # "I dont think i want over .5 hits as POTD material, write up
            # looks bad anyway." Batter hits O 0.5 is the classic juice
            # trap (see feedback_batter_hits_juice_trap_803); even Over 1.5
            # hits is high-variance one-game noise, not POTD material.
            # POTD needs stable prop shapes — pitcher outs/Ks, receiver yds,
            # rush yds. This filter fires ONLY for POTD selection; the
            # underlying prop still lives on the props tab.
            _pt = str(row.get('prop_type', '')).lower()
            if _pt in ('hits_over', 'hits_under'):
                continue
            direction = str(row.get('direction') or '').upper()
            odds = row.get('book_over_odds') if direction == 'OVER' else row.get('book_under_odds')
            # 2026-09-10 · strip direction suffix from prop_type in the display
            # string. DB stores prop_type as 'outs_under' / 'hits_over' /
            # 'ha_over' etc. — direction is already rendered separately, so
            # 'Jared Jones Under 15.5 outs_under' was showing the direction
            # twice. Drop the trailing _over/_under and pretty-print the stem.
            _raw_type = str(row.get('prop_type', '') or '')
            _stem = _raw_type.rsplit('_', 1)[0] if _raw_type.endswith(('_over','_under')) else _raw_type
            _PROP_LABEL = {'outs': 'Outs', 'hits': 'Hits', 'er': 'Earned Runs',
                           'ha': 'Hits Allowed', 'ks': 'Strikeouts', 'k': 'Strikeouts',
                           'tb': 'Total Bases', 'rbi': 'RBIs', 'r': 'Runs',
                           'sb': 'Stolen Bases', 'bb': 'Walks'}
            _pretty_type = _PROP_LABEL.get(_stem.lower(), _stem.replace('_', ' ').title())
            call_text = f"{row.get('player_name','?')} {direction.title()} {row.get('prop_line','?')} {_pretty_type}"
            out.append({
                'sport': sport,
                'game_id': row['game_id'],
                'call_market': 'prop',
                'call_side': direction,
                'call_line': row.get('prop_line'),
                'conviction': effective_conv,
                'call_text': call_text,
                'short_read': f"{row.get('prop_type','')} {direction.lower()} · conv {effective_conv}",
                'long_read': '',
                'generated_at': None,
                '_prop_meta': {
                    'player_name': row.get('player_name'),
                    'prop_type': row.get('prop_type'),
                    'odds': odds,
                    'matchup': row.get('matchup'),
                },
                '_source': 'top_prop',
            })
        if skipped_past:
            print(f"  ⏭  {sport} top_prop: skipped {skipped_past} props for games already played")
    return out


def run(game_date: str | None = None, threshold: int = 70,
        dry_run: bool = False, force: bool = False) -> None:
    gd = game_date or today_et()
    print(f"=== jerry_anchor_potd · {gd} (threshold={threshold}) ===")

    # 2026-09-09 POTD PUBLISH LOCK. Root fix for the user-reported
    # "POTD flipped 3 times today (Marlins → noPlay → Tigers)" bug.
    # Multiple crons in a day = multiple anchor writes = last-writer-wins
    # → user bets on morning POTD, sees different pick at 2pm.
    #
    # Once jerry_anchor_potd has published a Jerry-anchored decision for
    # a day (anchor='jerry_synthesis_v1'), subsequent crons SKIP the
    # re-anchor. First cron of the day wins. Same pattern as Sharp Card
    # publish lock (c6912775).
    #
    # Override with --force flag OR POTD_ALLOW_REPUBLISH=1 env var.
    # Bypass conditions: dry_run always runs; force respects user intent.
    _allow_republish = force or os.environ.get('POTD_ALLOW_REPUBLISH') == '1'
    if not dry_run and not _allow_republish:
        try:
            _existing = requests.get(
                f"{SUPABASE_URL}/rest/v1/jerry_cache",
                headers=H_READ,
                params={"cache_key": f"eq.best_bet_{gd}",
                        "select": "data,fetched_at"},
                timeout=10,
            )
            if _existing.status_code == 200 and _existing.json():
                _row = _existing.json()[0]
                _data = _row.get('data') or {}
                if isinstance(_data, str):
                    try:
                        import json as _j
                        _data = _j.loads(_data)
                    except Exception:
                        _data = {}
                _anchor = (_data or {}).get('anchor')
                if _anchor == 'jerry_synthesis_v1':
                    print(f"  🔒 best_bet_{gd} already Jerry-anchored "
                          f"({(_row.get('fetched_at') or '?')[:19]}) — skipping "
                          f"republish. Use --force or POTD_ALLOW_REPUBLISH=1 "
                          f"to override.")
                    return
        except Exception as _e:
            print(f"  ⚠ publish-lock check failed: {_e} — proceeding")

    # Pull Jerry reads across all eligible sports (POTD_SPORTS below).
    # UFC intentionally excluded per user 2026-07-31 — UFC stays a
    # standalone tab destination, not a cross-sport POTD candidate.
    # Add sports to POTD_SPORTS as their synthesizers ship.
    POTD_SPORTS = ["MLB", "NBA", "NFL", "NCAAF", "NCAAB"]
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_reads",
        headers=H_READ,
        params={"sport": f"in.({','.join(POTD_SPORTS)})",
                "game_date": f"eq.{gd}",
                "select": "sport,game_id,call_text,call_market,call_side,call_line,"
                          "conviction,short_read,long_read,generated_at",
                "order": "conviction.desc"},
        timeout=15,
    )
    reads = r.json() if r.status_code == 200 else []
    print(f"  {len(reads)} jerry_reads on the slate")

    # 2026-09-09: expand candidate pool with primary_play (real resolved
    # pick post ensemble+LR override) + top PRIME props. Prior version
    # limited POTD to jerry_reads narrative conviction which capped at
    # 40-68 many days, missing PRIME plays that had 76+ conviction
    # elsewhere in the pipeline.
    primary_candidates = _load_primary_play_candidates(gd, POTD_SPORTS)
    prop_candidates = _load_top_prop_candidates(gd, min_conv=80)
    print(f"  + {len(primary_candidates)} primary_play candidates (PRIME/STRONG across sports)")
    print(f"  + {len(prop_candidates)} top PRIME prop candidates (conv >= 80)")
    reads = list(reads) + primary_candidates + prop_candidates

    if not reads:
        print("  ⚠ no reads — POTD unchanged (falls back to whatever play_of_day picked)")
        return

    # Filter to eligible: conviction >= threshold, not a PASS or LEAN.
    # 2026-08-04: exclude 'lean' — Jerry's hedge state (conv 40-64) is not
    # POTD-worthy. POTD requires active PLAY conviction (≥ threshold, default 70).
    eligible = [r for r in reads
                if (r.get("conviction") or 0) >= threshold
                and (r.get("call_market") or "").lower() not in ("pass", "lean")]
    # Sort by conviction desc so downstream juice/LR gates still see the
    # best candidate first (jerry_reads was already sorted; merged list
    # needs re-sort).
    eligible.sort(key=lambda r: -int(r.get("conviction") or 0))
    if not eligible:
        print(f"  ⚠ no Jerry read at conviction >= {threshold} — Jerry passing on POTD today")
        _write_no_play(gd, dry_run, reads)
        return

    # POTD JUICE GATE (2026-08-03 user directive): "-200 or juicier ML shouldn't
    # be POTD — they're often anti-consensus plays priced heavy by books."
    # Fetch ML for each eligible ML pick and filter out ones at -200+ juice.
    # Non-ML picks (total/rl/prop) unaffected.
    ml_picks = [r for r in eligible if (r.get("call_market") or "").lower() == "ml"]
    if ml_picks:
        # Route each ML pick to its sport-specific context table (2026-08-03
        # universalization). Skip juice gate for sports without a
        # context table registered — best-effort, don't block the POTD.
        ml_lookup = {}
        by_sport = {}
        for r in ml_picks:
            by_sport.setdefault((r.get("sport") or "MLB").upper(), []).append(r)
        for sport, sport_picks in by_sport.items():
            ctx_table = _context_table(sport)
            if not ctx_table: continue
            game_ids = list({p["game_id"] for p in sport_picks})
            in_str = ','.join(f'"{g}"' for g in game_ids)
            resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/{ctx_table}",
                headers=H_READ,
                params={"game_id": f"in.({in_str})", "game_date": f"eq.{gd}",
                        "select": "game_id,home_ml_close,away_ml_close"},
                timeout=15,
            )
            rows = resp.json() if resp.status_code == 200 else []
            for c in (rows if isinstance(rows, list) else []):
                ml_lookup[c["game_id"]] = c
        filtered = []
        skipped_juice = []
        for r in eligible:
            if (r.get("call_market") or "").lower() != "ml":
                filtered.append(r); continue
            ctx_row = ml_lookup.get(r["game_id"], {})
            side = (r.get("call_side") or "").upper()
            pick_ml = ctx_row.get("home_ml_close") if side == "HOME" else ctx_row.get("away_ml_close")
            # 2026-09-12 juice gate loosened from -200 → -250 with LR guardrail.
            # -200 was tossing legitimate PRIME plays like Brewers ML conv 100
            # @ -207 (won 9/11, LR 1.00) and Dodgers ML conv 91 @ -209 (won,
            # LR 0.91). At -250 implied prob = 71.4%, so pairing with the LR
            # ≥ 0.60 gate downstream keeps EV positive: 71% market vs 60%+
            # model = -11pp minimum edge, which pays out on the roll. Anything
            # below -250 (e.g. -300 implied 75%) needs LR ≥ 0.75 to have EV,
            # which the ≥0.60 gate can't guarantee — hence the hard cap stays.
            if pick_ml is not None and pick_ml <= -250:
                ct = (r.get('call_text') or '?')[:30]
                skipped_juice.append(f"{ct} at {pick_ml} (below -250 cap)")
                continue
            filtered.append(r)
        if skipped_juice:
            print(f"  🚫 POTD juice gate: skipped {len(skipped_juice)} ML picks at -200+")
            for s in skipped_juice[:5]:
                print(f"      · {s}")
        eligible = filtered

    if not eligible:
        print(f"  ⚠ no eligible reads after juice gate — Jerry passing")
        _write_no_play(gd, dry_run, reads)
        return

    # 2026-09-08 LR CONFIDENCE GATE (Option A per project_data_infrastructure_priorities_908).
    # Filter out picks whose underlying LR probability is < 0.60 — the pipeline
    # was surfacing coin-flip picks (LR 0.55-0.59) as POTD because sweat_score
    # inflated them. Users see "Jerry 86/100" but the model was actually 57%.
    # That's not honest stats-backed analysis; that's a display artifact
    # dressing up a coin flip.
    #
    # Fetches LR shadow probability from primary_play._lr_ml_shadow.p_home_win
    # (or ._lr_total_shadow.p_over) for each eligible pick's game. If the LR
    # probability supporting the pick side is < 0.60, skip. Non-LR-scored
    # picks (no _lr_*_shadow fields) pass through unchanged (backwards compat
    # — sports without LR wired stay eligible on conviction alone).
    #
    # 2026-09-09: Fixed field paths — was checking pp["_lr_p_home_win"]
    # which never existed; real path is pp["_lr_ml_shadow"]["p_home_win"].
    # Gate was silently no-op across entire slate before this fix.
    lr_gated = []
    lr_skipped = []
    for r in eligible:
        sport = (r.get("sport") or "MLB").upper()
        ctx_table = _context_table(sport)
        if not ctx_table:
            lr_gated.append(r); continue
        pp_resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/{ctx_table}",
            headers=H_READ,
            params={"game_id": f"eq.{r.get('game_id')}",
                    "game_date": f"eq.{gd}",
                    "select": "primary_play"},
            timeout=10,
        )
        pp_data = pp_resp.json() if pp_resp.status_code == 200 else []
        pp = pp_data[0].get("primary_play") if pp_data else None
        if isinstance(pp, str):
            try: pp = json.loads(pp)
            except (json.JSONDecodeError, TypeError): pp = {}
        pp = pp or {}
        call_mkt = (r.get("call_market") or "").lower()
        call_side = (r.get("call_side") or "").upper()
        ml_shadow = pp.get("_lr_ml_shadow") or {}
        tot_shadow = pp.get("_lr_total_shadow") or {}
        p_support = None
        if call_mkt == "ml" and ml_shadow.get("p_home_win") is not None:
            p_home = float(ml_shadow["p_home_win"])
            p_support = p_home if call_side == "HOME" else (1 - p_home)
        elif call_mkt == "total" and tot_shadow.get("p_over") is not None:
            p_over = float(tot_shadow["p_over"])
            p_support = p_over if call_side == "OVER" else (1 - p_over)
        # 2026-09-12 EXTEND LR gate to RL / spread. Prior gap: only ml/total
        # were checked, so RL picks bypassed the gate entirely. Root cause of
        # 9/11 POTD ("Rangers RL +1.5" conv 80 selected while every graded
        # ML/total >= conv 79 won). RL support = p_home_win (or 1-p_home_win)
        # since RL and ML share a directional prior. Slightly looser gate
        # (0.55 not 0.60) because RL +/- 1.5 has ~40% of the win margin
        # compared to pure ML, so the LR support needn't be as high to be
        # a positive-EV pick.
        elif call_mkt in ("rl", "spread") and ml_shadow.get("p_home_win") is not None:
            p_home = float(ml_shadow["p_home_win"])
            p_support = p_home if call_side == "HOME" else (1 - p_home)
            if p_support < 0.55:
                ct = (r.get('call_text') or '?')[:30]
                lr_skipped.append(f"{ct} RL p={p_support:.2f} (<0.55)")
                continue
            lr_gated.append(r); continue
        if p_support is not None and p_support < 0.60:
            ct = (r.get('call_text') or '?')[:30]
            lr_skipped.append(f"{ct} p={p_support:.2f}")
            continue
        lr_gated.append(r)
    if lr_skipped:
        print(f"  🚫 POTD LR-confidence gate: skipped {len(lr_skipped)} picks with LR support < 0.60")
        for s in lr_skipped[:5]:
            print(f"      · {s}")
    eligible = lr_gated

    if not eligible:
        print(f"  ⚠ no eligible reads after LR gate — Jerry passing (stats-backed accuracy over false conviction)")
        _write_no_play(gd, dry_run, reads)
        return

    winner = eligible[0]
    gid = winner["game_id"]
    conv = winner["conviction"]
    winner_sport = (winner.get("sport") or "MLB").upper()

    # Reconstruct display text when call_text is None (2026-08-04).
    # Jerry's LLM output sometimes omits CALL_TEXT for prop reads even
    # though SIDE + LINE + market are populated. Fallback synthesizes a
    # sensible display so the app doesn't render literal "?" in the POTD
    # box (the whole point of the anchor is a headline play).
    def _synthesize_display(w):
        raw = w.get("call_text")
        if raw and raw.strip() and raw.strip() != "?":
            return raw
        mk = (w.get("call_market") or "").lower()
        side = w.get("call_side") or ""
        line = w.get("call_line")
        short = w.get("short_read") or ""
        if mk == "prop":
            # Extract player name from first sentence of short_read
            first_sent = short.split(".")[0].strip() if short else ""
            # Player name is usually the first 2-3 capitalized words
            import re as _re
            m = _re.match(r"^\**([A-Z][a-zA-ZÀ-ÿ.'-]+(?:\s+[A-Z][a-zA-ZÀ-ÿ.'-]+){0,3})", first_sent)
            player = m.group(1).strip() if m else "Prop"
            line_str = f" {line}" if line is not None else ""
            return f"{player} {side.title()}{line_str}"
        if mk == "total":
            return f"{side.title()} {line}" if line is not None else side.title()
        if mk == "ml":
            return f"{side.title()} ML"
        if mk == "rl":
            return f"{side.title()} RL{f' {line:+g}' if line is not None else ''}"
        return "Play of the Day"

    call = _synthesize_display(winner)
    print(f"  🏆 POTD winner: {call} (conviction={conv}, game_id={gid}, sport={winner_sport})")

    # Route to sport-specific context table (2026-08-03 universalization).
    # Select fields available across sports; MLB-specific (venue, temperature,
    # nrfi_score, lineup_confirmed) may be null for non-MLB but PostgREST
    # returns them anyway.
    ctx_table = _context_table(winner_sport)
    if not ctx_table:
        print(f"  ⚠ no context table registered for sport={winner_sport} — aborting")
        return
    gc = requests.get(
        f"{SUPABASE_URL}/rest/v1/{ctx_table}",
        headers=H_READ,
        params={"game_id": f"eq.{gid}", "game_date": f"eq.{gd}",
                "select": "home_team,away_team"},
        timeout=10,
    )
    ctx_rows = gc.json() if gc.status_code == 200 else []
    if not ctx_rows:
        print("  ⚠ no game_context for winner — abort")
        return
    ctx = ctx_rows[0]

    # Build the payload — matches existing jerry_cache best_bet schema
    # 2026-08-02: include `matchup` explicitly so generate_sweat_card's
    # play_signature dedup can collapse POTD with the plain Jerry-anchored
    # ML slot on the same game (both are the same bet). Without this,
    # game field on POTD ended up None and dedup missed.
    matchup_str = f"{ctx['away_team']} @ {ctx['home_team']}"

    # 2026-08-30 fix: ALWAYS derive team name from ctx + call_side, do
    # not trust stored call_text. Was: "only overwrite when call is
    # literal 'Home ML'/'Away ML'" — but a race today (Miami Marlins as
    # POTD) rendered "Washington Nationals ML (Jerry 97)" while Jerry's
    # actual call_side=AWAY (Miami). Root cause was call_text ending up
    # as the wrong team name via some earlier synth path. Single-source-
    # of-truth: ctx.away_team/home_team + call_side determines the team
    # name. call_text becomes a display fallback only.
    _mkt = (winner.get("call_market") or "").lower()
    _side = (winner.get("call_side") or "").upper()
    if _mkt == "ml":
        team = ctx["home_team"] if _side == "HOME" else ctx["away_team"] if _side == "AWAY" else None
        if team:
            call = f"{team} ML"
    elif _mkt == "rl":
        team = ctx["home_team"] if _side == "HOME" else ctx["away_team"] if _side == "AWAY" else None
        if team:
            # Preserve line suffix if present in original call
            line = winner.get("call_line")
            line_str = f" {line:+g}" if line is not None else ""
            call = f"{team} RL{line_str}"

    payload_data = {
        "sport": winner_sport,  # 2026-09-09: was hardcoded 'MLB'
        "matchup": matchup_str,
        "_source": winner.get('_source', 'jerry_reads'),  # 2026-09-09 source tracking
        "game": {
            "away_team": ctx["away_team"],
            "home_team": ctx["home_team"],
            "matchup": matchup_str,
            "commence_time": None,  # play_of_day carries this; Jerry doesn't need it
        },
        "leanDisplay": f"{call} (Jerry {conv}/100)",
        "score": {"total": conv, "source": "jerry_conviction"},
        "confidence": _conviction_tier(conv),
        # 2026-09-08 defense-in-depth: if jerry_pick_scrub left a stale
        # "Model recomputed to X" template in short_read pointing to a
        # different pick than what the POTD is actually calling, that
        # text would render as the POTD write-up and confuse users.
        # Root case: 9/8 POTD showed "Model recomputed to Houston +1.5"
        # for a Phillies ML pick. jerry_pick_scrub bug was the primary
        # fix but this belt-and-suspenders check catches any future
        # scrub-template contamination before it hits the narrative.
        # Fall back to leanDisplay in that case; generate_potd_narrative
        # will replace with a real Claude write-up when it next runs.
        "narrative": (
            (winner.get("short_read") or "")
            if "model recomputed to" not in (winner.get("short_read") or "").lower()
            else (winner.get("long_read") or "")
        ) or "",
        "context": {
            "venue": ctx.get("venue"),
            "temperature": ctx.get("temperature"),
            "nrfi_score": ctx.get("nrfi_score"),
            "lean_bet": winner.get("call_market"),
            "jerry_anchored": True,
            "conviction": conv,
        },
        "generatedAt": gd,
        "pipelineGenerated": True,
        "anchor": "jerry_synthesis_v1",
    }
    narrative_line = f"Play of the Day: {ctx['away_team']} @ {ctx['home_team']} | {call} (Jerry {conv})"

    if dry_run:
        print(f"  [DRY] would upsert best_bet_{gd} · {narrative_line}")
        return

    # Upsert jerry_cache best_bet — actual unique constraint is on cache_key
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
        headers=H_WRITE,
        json={
            "cache_key": f"best_bet_{gd}",
            "game_id": f"best_bet_{gd}",
            "sport": "MLB",
            "narrative": narrative_line,
            "data": payload_data,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=15,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠ jerry_cache upsert {r.status_code}: {r.text[:200]}")
        return
    print(f"  ✅ jerry_cache best_bet_{gd} anchored to Jerry")

    # Mirror to daily_best_bet_history
    try:
        hr = requests.post(
            f"{SUPABASE_URL}/rest/v1/daily_best_bet_history?on_conflict=bet_date",
            headers=H_WRITE,
            json={
                "bet_date": gd,
                "sport": "MLB",
                "game": f"{ctx['away_team']} @ {ctx['home_team']}",
                "lean": f"{call} (Jerry {conv}/100)",
                "sweat_score": conv,  # column name preserved; source is now Jerry
                "result": "Pending",
            },
            timeout=15,
        )
        if hr.status_code not in (200, 201, 204):
            print(f"  ⚠ history mirror {hr.status_code}: {hr.text[:200]}")
        else:
            print(f"  ✅ daily_best_bet_history mirrored")
    except Exception as e:
        print(f"  ⚠ history mirror failed: {e}")


def _write_no_play(game_date: str, dry_run: bool, reads: list) -> None:
    """Write a 'Jerry passing' payload when no read clears the threshold.
    Preserves the same shape play_of_day used for no-play days
    (top-level noPlay + reason keys)."""
    top_conv = max((r.get("conviction") or 0) for r in reads)
    reason = (f"Jerry's highest conviction today is {top_conv} — below the "
              f"POTD threshold. Full slate available; no headline play locked.")
    if dry_run:
        print(f"  [DRY] would upsert best_bet_{game_date} · noPlay=True · {reason}")
        return
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
        headers=H_WRITE,
        json={
            "cache_key": f"best_bet_{game_date}",
            "game_id": f"best_bet_{game_date}",
            "sport": "MLB",
            "narrative": f"Jerry passing on POTD ({reason})",
            "data": {"noPlay": True, "reason": reason,
                     "anchor": "jerry_synthesis_v1", "generatedAt": game_date},
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=15,
    )
    if r.status_code in (200, 201, 204):
        print(f"  ✅ POTD marked 'no play' by Jerry (top conv = {top_conv})")
    else:
        print(f"  ⚠ upsert {r.status_code}: {r.text[:200]}")

    # 2026-09-09 DUAL-WRITE FIX. Root fix for POTD source-of-truth
    # disagreement: jerry_cache said noPlay while daily_best_bet_history
    # still had a Marlins entry from earlier pipelineGenerated write.
    # Users saw "no play tonight" on home page but "today's POTD: Marlins"
    # on Receipts. Same drift class as grade_potd.py earlier tonight.
    # Now: when Jerry marks noPlay, also mark history to No Play so all
    # surfaces agree. Non-fatal — jerry_cache is authoritative anyway.
    try:
        hr = requests.patch(
            f"{SUPABASE_URL}/rest/v1/daily_best_bet_history",
            headers=H_WRITE,
            params={"bet_date": f"eq.{game_date}"},
            json={"lean": "No Play (discipline pass)",
                  "result": "No Play"},
            timeout=15,
        )
        if hr.status_code in (200, 204):
            print(f"  ✅ history mirrored to No Play")
        elif hr.status_code == 404:
            pass  # no history row for today — nothing to mirror
        else:
            print(f"  ⚠ history mirror failed {hr.status_code}: {hr.text[:120]}")
    except Exception as e:
        print(f"  ⚠ history mirror exception {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date")
    p.add_argument("--threshold", type=int, default=70)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="Bypass publish lock — overwrites today's POTD "
                        "even if already Jerry-anchored. Admin-only.")
    args = p.parse_args()
    run(game_date=args.date, threshold=args.threshold,
        dry_run=args.dry_run, force=args.force)
