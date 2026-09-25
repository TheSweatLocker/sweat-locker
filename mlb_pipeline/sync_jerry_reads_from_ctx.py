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
        from jerry_reads_dual_write import parse_synthesis, strip_section_markers
    except Exception as e:
        print(f'  ⚠ parser unavailable ({e}) — refusing to publish raw '
              f'narrative that may contain markers')
        return None, None
    parsed = parse_synthesis(narrative) or {}
    short = strip_section_markers(parsed.get('short_read'), 'short_read')
    long_ = strip_section_markers(parsed.get('long_read') or narrative, 'long_read')
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
            if rows and rows[0].get('prompt_version') and \
               not str(rows[0].get('prompt_version')).endswith('_ctx_bridge_v1'):
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
            short_read = _sub or 'Analysis pending — Jerry is reviewing the tape.'

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
                        'select': 'short_read,long_read,prompt_version'},
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
    ap.add_argument('--window', type=int, default=1,
                    help='Sweep from --date forward N days (default 1 = just that date)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    base = args.date or (dt.datetime.utcnow() - dt.timedelta(hours=4)).date().isoformat()
    y,m,d = (int(x) for x in base.split('-'))
    base_d = dt.date(y,m,d)
    dates = [(base_d + dt.timedelta(days=i)).isoformat() for i in range(args.window)]
    sports = list(SPORT_CONFIG) if args.sport == 'ALL' else [args.sport]

    print(f'=== sync_jerry_reads_from_ctx · {sports} · {dates[0]}..{dates[-1]}{" [DRY]" if args.dry_run else ""} ===')
    for gd in dates:
        for sp in sports:
            c, w, s = sync_sport(sp, gd, dry=args.dry_run)
            if c or w:
                print(f'  {sp} {gd}: checked={c} wrote={w} skipped={s}')


if __name__ == '__main__':
    main()
