#!/usr/bin/env python3
"""sync_jerry_reads_from_ctx — bridge script for sports where the Jerry
LLM writes prose to jerry_cache but never populates jerry_reads.

Root cause (2026-08-28): NCAAF/NHL/NBA/UFC prompt templates emit narrative
prose only — no structured CALL block (MARKET/SIDE/LINE/CALL_TEXT). So
parse_synthesis returns empty and dual-write to jerry_reads never fires.
App reads jerry_reads for the badge and shows placeholder text.

This script bridges the gap:
  - For each game with `primary_play` on ctx
  - Read the narrative from jerry_cache (`game_read_{gid}_{date}`) if exists
  - UPSERT jerry_reads with call_market/side/line from primary_play (mechanical
    truth) + short_read/long_read from jerry_cache narrative (LLM prose)

Aligns with the Jerry-as-narrator architecture: ensemble is the pick,
Jerry writes prose about it. This is the deterministic version.

Sport-universal. CLI:
  python sync_jerry_reads_from_ctx.py                     # today, all sports
  python sync_jerry_reads_from_ctx.py --sport NCAAF
  python sync_jerry_reads_from_ctx.py --sport NCAAF --date 2026-08-29
  python sync_jerry_reads_from_ctx.py --dry-run
"""

from __future__ import annotations
import argparse, os, sys, datetime as dt
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests
SB  = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
H   = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW  = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

SPORT_CONFIG = {
    'MLB':   {'ctx': 'mlb_game_context'},
    'NCAAF': {'ctx': 'ncaaf_game_context'},
    'NFL':   {'ctx': 'nfl_game_context'},
    'NCAAB': {'ctx': 'ncaab_game_context'},
    'NBA':   {'ctx': 'nba_game_context'},
    'NHL':   {'ctx': 'nhl_game_context'},
}


def _derive_call_text(pp: dict, home: str, away: str) -> str | None:
    if not pp: return None
    ptype = (pp.get('type') or '').lower()
    side  = (pp.get('side') or '').upper()
    line  = pp.get('line')
    label = pp.get('label')
    if label: return label
    if ptype == 'ml':
        if side == 'HOME': return f'{home} ML'
        if side == 'AWAY': return f'{away} ML'
    if ptype == 'total' and side in ('OVER','UNDER'):
        return f'{side.capitalize()}' + (f' {line}' if line is not None else '')
    if ptype in ('rl','spread') and side in ('HOME','AWAY'):
        team = home if side == 'HOME' else away
        return f'{team} ' + (f'{line}' if line is not None else '')
    return None


def _parse_narrative(narrative: str) -> tuple[str | None, str | None]:
    """Split a jerry_cache narrative into publishable (short, long).

    2026-09-24 — THIS SCRIPT PUBLISHED PARSER MARKERS TO SUBSCRIBERS.

    The bridge was written for NCAAF/NHL/NBA/UFC, whose prompt templates emit
    prose only, so it treated `narrative` as ready to publish: short_read was
    its first 200 characters and long_read was the whole string. NFL's template
    DOES emit ---SHORT--- / ---LONG--- / ---CALL--- sections, and this script
    runs for NFL too, so it wrote:

        short_read = "---SHORT---\\nLA's passing offense ranks 11th..."
        long_read  = the entire marked-up narrative

    Three NFL reads shipped that way. I repaired them by hand in the morning,
    then added a marker guard to jerry_reads_dual_write.upsert_jerry_read, then
    a second one to generate_nfl_game_reads.upsert_jerry_read_nfl — and the
    markers came back both times, because THIS is the writer that produced
    them. Identified from the row itself: prompt_version `nfl_ctx_bridge_v1`,
    no `source` in input_snapshot, written Thu 17:31 ET.

    So parse first and publish sections, exactly like the real generators. When
    the narrative has no markers the parser's unmarked-prose fallback returns
    it unchanged, which is the NCAAF/NHL behaviour this script was built for —
    nothing regresses for those sports.
    """
    if not narrative:
        return None, None
    try:
        from jerry_reads_dual_write import (parse_synthesis,
                                            strip_section_markers,
                                            strip_markdown_emphasis)
    except Exception as e:
        print(f'  ⚠ parser unavailable ({e}) — refusing to publish raw '
              f'narrative that may contain markers')
        return None, None
    parsed = parse_synthesis(narrative) or {}
    # Markers first (that re-parses, so it needs the parser's shape), then
    # markdown emphasis — the app has no markdown renderer, so "**MATCHUP:**"
    # reaches a subscriber as four literal asterisks.
    short = strip_markdown_emphasis(
        strip_section_markers(parsed.get('short_read'), 'short_read'))
    long_ = strip_markdown_emphasis(
        strip_section_markers(parsed.get('long_read') or narrative, 'long_read'))
    if short:
        short = short.strip()
        # Keep the old first-sentence shape when the parsed SHORT is long-form.
        if len(short) > 400:
            end = short.find('. ')
            short = (short[:end + 1] if 0 < end < 400
                     else short[:400].rstrip() + '…')
    return (short or None), (long_ or None)


def sync_sport(sport: str, gd: str, dry: bool = False) -> tuple[int, int, int]:
    cfg = SPORT_CONFIG[sport]
    r = requests.get(
        f'{SB}/rest/v1/{cfg["ctx"]}',
        params={'game_date': f'eq.{gd}', 'primary_play': 'not.is.null',
                'select': 'game_id,home_team,away_team,primary_play'},
        headers=H, timeout=30,
    )
    if r.status_code != 200:
        print(f'  {sport}: ctx fetch failed {r.status_code}')
        return (0, 0, 0)
    ctx_rows = r.json() or []
    if not ctx_rows:
        print(f'  {sport} {gd}: no ctx rows with primary_play')
        return (0, 0, 0)

    checked = wrote = skipped = 0
    for c in ctx_rows:
        checked += 1
        gid = c['game_id']
        home = c.get('home_team', ''); away = c.get('away_team', '')
        pp = c.get('primary_play') or {}
        ptype = (pp.get('type') or '').lower()
        side  = (pp.get('side') or '').upper()
        if ptype not in ('ml', 'spread', 'rl', 'total'):
            skipped += 1
            continue

        # 2026-08-29 CRITICAL FIX: skip if a REAL synthesis row already
        # exists for this (sport, game_id, game_date). Sync bridge was
        # UPSERTING with `Prefer: resolution=merge-duplicates` and
        # OVERWRITING real Jerry synth output ('synthesis_v1' prompt)
        # with placeholder bridge output ('mlb_ctx_bridge_v1' + 'Analysis
        # pending' short_read). Every workflow cycle: synth wrote reads,
        # then sync bridge ran downstream and wiped them. All 17 MLB
        # games showed "Analysis pending" all day 8/29 as a result.
        existing = requests.get(
            f'{SB}/rest/v1/jerry_reads',
            params={'sport': f'eq.{sport}', 'game_id': f'eq.{gid}',
                    'game_date': f'eq.{gd}',
                    'select': 'prompt_version'},
            headers=H, timeout=10,
        )
        if existing.status_code == 200:
            rows = existing.json()
            # ══ 2026-10-29 · prompt_version IS NOT PROOF OF PROSE ════════
            # This skipped on the version string ALONE, which is cruder than
            # the content test further down and ran before it, so the
            # content test never got a say.
            #
            # Tonight's CAR @ GB carried prompt_version
            # 'nfl_game_read_v2_2026-08-06' with a short_read and long_read
            # that were both the same 45 characters:
            #     "GB ML · GB · 63% vs 66% implied · -3.4pp edge"
            # — the scorer's own summary line, not a write-up. The pick was
            # then changed to CAR +3.5 and this bridge refused to carry it
            # across, leaving the app showing GB ML for a game that night.
            #
            # The 08-29 guard this implements is still right: a real
            # generated read must never be replaced by a bridge stub. It
            # just has to judge the ROW, not its label. So the version check
            # now also requires substantive content — which is the same test
            # the placeholder branch below applies, hoisted so the two
            # cannot disagree.
            if rows:
                _pv = str(rows[0].get('prompt_version') or '')
                _s = (rows[0].get('short_read') or '').strip()
                _l = (rows[0].get('long_read') or '').strip()
                _has_prose = len(_s) >= 60 and len(_l) >= 300
                if _pv and not _pv.endswith('_ctx_bridge_v1') and _has_prose:
                    skipped += 1
                    continue

        # Try to find narrative from jerry_cache. cache_key format is
        # game_read_{game_id}_{generation_date} — generation_date != game_date
        # (LLM often runs day-of-writing not game-day). Match by prefix and
        # take the most recent narrative for this game_id.
        prefix = f'game_read_{gid}_'
        cr = requests.get(
            f'{SB}/rest/v1/jerry_cache',
            params={'cache_key': f'like.{prefix}%', 'sport': f'eq.{sport}',
                    'select': 'narrative,fetched_at',
                    'order': 'fetched_at.desc', 'limit': '1'},
            headers=H, timeout=15,
        )
        narrative = None
        if cr.status_code == 200 and cr.json():
            narrative = cr.json()[0].get('narrative')

        call_text = _derive_call_text(pp, home, away)
        long_read = narrative
        if narrative:
            short_read, long_read = _parse_narrative(narrative)
        if not narrative or not short_read:
            # 2026-09-19: prefer the engine's own one-line rationale over a
            # bare "pending". primary_play['sub'] reads like "Supervised
            # total model backs Under · 80% confidence" — true, publishable
            # and already computed. A user seeing WHY we like the pick beats
            # a user seeing that we have not written it up yet.
            # Falls back to the pending copy only when the engine gave no
            # rationale, which is an honest empty state rather than a
            # promise of a refresh we may not deliver (see the matching
            # note in jerry_pick_scrub.py).
            _sub = (pp.get('sub') or '').strip() if isinstance(pp, dict) else ''
            short_read = _sub or 'Analysis pending - Jerry is reviewing the tape.'

        # == 2026-10-28 . THE ENGINE'S VERDICT OUTRANKS THE NARRATIVE ==
        # When the MC dissent gate fires, defensive_gates demotes the tier,
        # drives conviction to 0 via tier_cap {'COVERAGE': 0} and rewrites
        # primary_play['sub'] to '! Engine passed: MC sim has our side at
        # N% win prob'. Its own comment says why:
        #
        #   "a PRIME 'sharp money is here . H2H dominant' narrative on a
        #    COVERAGE/conv=0 pick reads as a lie (Rays 8/30 canonical)"
        #
        # It fixed `sub`. It did not fix short_read, which is what users
        # actually read -- and the block above only falls back to `sub`
        # when the LLM narrative is MISSING. So the narrative won, and it
        # was written before the gate ran. Measured on NHL: 15 picks carry
        # an _mc_dissent, 10 of them at conviction 0, ALL 15 became reads
        # with call_text and ALL 15 have receipts in the published record.
        # One of them reads 'Markstrom is confirmed and running a 0.9167
        # save percentage with a plus-2.16 GSAA ... a 5.31-goal chasm'
        # above a conviction of 0.
        #
        # NHL hits this far more than anywhere else -- conviction 0 on 10.1%
        # of playable reads against NBA 0.0%, NCAAF 0.0%, NFL 0.7%, MLB 3.0%
        # -- which is why it looked like 'NHL has no conviction'.
        #
        # The pick is NOT suppressed. It stays visible, the record stays
        # whole, and the engine's verdict simply leads instead of being
        # buried: a user reading a confident goalie argument is told first
        # that the engine passed on it.
        if isinstance(pp, dict) and pp.get('_mc_dissent'):
            _verdict = (pp.get('sub') or '').strip()
            if _verdict and short_read and not short_read.startswith(_verdict):
                short_read = f'{_verdict} - {short_read}'

        payload = {
            'sport': sport,
            'game_id': gid,
            'game_date': gd,
            'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'prompt_version': f'{sport.lower()}_ctx_bridge_v1',
            'call_market': ptype,
            'call_side':   side or None,
            'call_line':   pp.get('line'),
            'call_text':   call_text,
            'short_read':  short_read,
            'long_read':   long_read,
            'conviction':  int(pp.get('conviction') or 0) if pp.get('conviction') else None,
        }
        matchup = f'{away[:14]} @ {home[:14]}'

        # 2026-09-24 — THIS BRIDGE FILLS GAPS. IT DOES NOT OVERWRITE PROSE.
        #
        # It upserts on (sport, game_id, game_date), so every run replaced
        # whatever was already there. That is how three NFL reads generated by
        # generate_nfl_game_reads at 11:39 Thu were replaced at 17:31 Thu by
        # this script's marked-up excerpt — inside the Thu-8am week lock that
        # nfl_week_write_locked() exists to enforce and that this script never
        # consulted.
        #
        # Checking the lock per sport would mean importing two lock functions
        # today and one more for every sport that gets a lock later. The
        # invariant that actually matters is simpler and sport-agnostic: a
        # bridge whose short_read is a first-sentence excerpt, or an engine sub
        # line, or "Analysis pending", must never replace a real generated
        # read. So skip when the existing row already holds prose, regardless
        # of day or sport.
        #
        # Placeholders ARE still replaced — repairing those is the whole point
        # of this script (see the NHL case where 37 reads were engine subs).
        try:
            ex = requests.get(
                f'{SB}/rest/v1/jerry_reads',
                params={'sport': f'eq.{sport}', 'game_id': f'eq.{gid}',
                        'game_date': f'eq.{gd}',
                        # 2026-10-27: market/side/line and the price come
                        # back too, so a market change can invalidate the
                        # price it was computed for. See below.
                        'select': 'short_read,long_read,prompt_version,'
                                  'call_market,call_side,call_line,'
                                  'price_american'},
                headers=H, timeout=15)
            rows = ex.json() if ex.status_code == 200 else []
        except Exception as _e:
            print(f'  ⚠ {matchup}: existing-read check failed ({_e}) — '
                  f'skipping rather than risk overwriting prose')
            skipped += 1
            continue
        if rows:
            _cur = (rows[0].get('short_read') or '').strip()
            _curlong = (rows[0].get('long_read') or '').strip()
            _placeholder = (
                not _cur
                or _cur.startswith('Analysis pending')
                or _cur.startswith('Model conviction on')
                or _cur.startswith('Supervised total model backs')
                or len(_cur) < 60
            )
            # A real generated read has a substantive long_read too; the
            # bridge's own output often does not.
            if not _placeholder and len(_curlong) >= 300:
                print(f'  🔒 {matchup}: existing read is prose '
                      f'({len(_cur)}c short / {len(_curlong)}c long, '
                      f'{rows[0].get("prompt_version")}) — leaving it alone')
                skipped += 1
                continue

        # ══ 2026-10-27 · A MARKET CHANGE MUST INVALIDATE THE PRICE ═══════
        # This upsert rewrites call_market/call_side/call_line and left the
        # price fields untouched, so a read priced as an ML and then
        # regenerated here as a SPREAD kept the moneyline price.
        #
        # Measured on jerry_reads: 55 rl reads carry a price impossible for a
        # spread, and 53 of them were regenerated AFTER being priced --
        # NBA 16 of 16, NHL 28 of 28, NCAAF 8 of 8, NFL 1 of 1. The regen
        # gap is consistently 220-330 minutes, so the read is priced in the
        # afternoon and rewritten about four hours later.
        #
        # Example: "Philadelphia 76ers -12.5" stored -669. line_history has
        # 427 quotes for spread/home/-12.5 between -110 and -118; -669 is the
        # MEDIAN OF THE MONEYLINE (home ML ran -850..-721). The price was
        # correct for the market it was computed against, and that market
        # then changed underneath it.
        #
        # Every ROI, breakeven and edge figure on those rows was wrong, which
        # is why NBA spread records could not be trusted at all. A NULL is
        # the honest content -- price_picks.py re-prices a NULL on its next
        # run, and a NULL is visibly absent where a stale number is not.
        #
        # feedback_late_data_must_invalidate_derived_output.
        if rows:
            _old_mkt = str(rows[0].get('call_market') or '')
            _old_side = str(rows[0].get('call_side') or '')
            _old_line = rows[0].get('call_line')
            _new_mkt = str(ptype or '')
            _new_side = str(side or '')
            _new_line = pp.get('line')

            def _same_line(x, y):
                if x is None and y is None:
                    return True
                try:
                    return abs(float(x) - float(y)) < 1e-9
                except (TypeError, ValueError):
                    return str(x) == str(y)

            _changed = (_old_mkt != _new_mkt or _old_side != _new_side
                        or not _same_line(_old_line, _new_line))
            if _changed and rows[0].get('price_american') is not None:
                payload.update({
                    'price_american': None, 'price_implied': None,
                    'price_best': None, 'price_best_book': None,
                    'price_books_n': None, 'priced_at': None,
                })
                print(f'  ⚠ {matchup}: pick changed '
                      f'{_old_mkt}/{_old_side}/{_old_line} -> '
                      f'{_new_mkt}/{_new_side}/{_new_line} — clearing the '
                      f'stale price ({rows[0].get("price_american")})')

        if dry:
            print(f'  DRY {matchup}: {ptype}/{side} "{call_text}"  narrative={bool(narrative)}')
            wrote += 1
            continue
        r = requests.post(
            f'{SB}/rest/v1/jerry_reads?on_conflict=sport,game_id,game_date',
            headers=HW, json=payload, timeout=15,
        )
        if r.status_code in (200, 201, 204):
            wrote += 1
            print(f'  ✓ {matchup}: {call_text}  ({sport})')
        else:
            print(f'  ✗ {matchup}: {r.status_code} {r.text[:150]}')
    return (checked, wrote, skipped)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=['ALL'] + list(SPORT_CONFIG),
                    default='ALL')
    ap.add_argument('--date', help='YYYY-MM-DD (default: today ET). Use --window N to sweep N days ahead too.')
    # 2026-09-29: default was a hardcoded 1 and the NCAAF workflow passed 7 —
    # the FIFTH hand-written copy of "how far ahead does the app show games",
    # and the narrowest, which made it the real binding constraint on NCAAF
    # jerry_reads coverage rather than the generator's 10 days. On a Wednesday
    # the app's tomorrow tab reaches +13, so a 7-day sweep left most of next
    # play-week with a ctx row and no read. Default is now 'auto': weekly
    # sports sweep to season_calendar.read_horizon (the app's own window),
    # daily sports stay at 1. Pass an integer to override.
    ap.add_argument('--window', default='auto',
                    help="Days to sweep forward from --date. 'auto' (default) "
                         "= through end of next play-week for weekly sports, "
                         "1 day for daily sports. An integer forces that count.")
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    base = args.date or (dt.datetime.utcnow() - dt.timedelta(hours=4)).date().isoformat()
    y,m,d = (int(x) for x in base.split('-'))
    base_d = dt.date(y,m,d)
    sports = list(SPORT_CONFIG) if args.sport == 'ALL' else [args.sport]

    from season_calendar import read_horizon

    def _dates_for(sport):
        """Date sweep for one sport. Per-sport because 'auto' differs by sport."""
        if str(args.window).lower() != 'auto':
            n = max(1, int(args.window))
            return [(base_d + dt.timedelta(days=i)).isoformat() for i in range(n)]
        hz = read_horizon(sport, on=base_d)
        if hz is None:            # daily sport — no play-week concept
            return [base_d.isoformat()]
        n = (hz - base_d).days + 1
        return [(base_d + dt.timedelta(days=i)).isoformat() for i in range(max(1, n))]

    print(f'=== sync_jerry_reads_from_ctx · {sports} · from {base_d} '
          f'· window={args.window}{" [DRY]" if args.dry_run else ""} ===')
    for sp in sports:
        dates = _dates_for(sp)
        print(f'  {sp}: {dates[0]}..{dates[-1]} ({len(dates)}d)')
        for gd in dates:
            c, w, s = sync_sport(sp, gd, dry=args.dry_run)
            if c or w:
                print(f'    {sp} {gd}: checked={c} wrote={w} skipped={s}')


if __name__ == '__main__':
    main()
