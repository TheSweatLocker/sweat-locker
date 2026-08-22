"""classify_line_moves — cross-reference line movement with sharp / public
splits to emit SHARP_MOVE / PUBLIC_MOVE / RLM / CONSENSUS tags.

Runs AFTER detect_line_movement.py (which populates raw steam/RLM/limit
flags from line_history alone). This script upgrades each flag with a
proper classification by cross-referencing:
  * line_history       — where did the line move to? (per-book snapshots)
  * line_snapshot      — OddsCrowd money% / bets% (sharp vs public split)
  * fadereport_signals — cross-verification split (if table exists)

Per project_sharp_money_fade_808:
  * When OddsCrowd money% ≥ 60 AND bets% low → sharp side (line SHOULD move here)
  * When line moves TO side that has high bets% but low money% → public trap
  * When line moves AWAY from side with heavy public $ → RLM (classic sharp signal)

Writes classification + supporting split snapshot back to line_movement_flags.
Sport-universal — driven by line_movement_config.SPORT_CONFIG.

CLI
  python classify_line_moves.py                       # all sports, today
  python classify_line_moves.py --sport MLB
  python classify_line_moves.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

from line_movement_config import get_config, classify_split, combine_classifications
# 3rd source: Cleatz (added 2026-08-15 pm)
# Cleatz publishes full-slate splits with both handle% and bets% per side
# for MLB / NFL / CFB. When all 3 sources (OC + FR + Cleatz) agree on
# sharp side → SHARP_TRIPLE_CONFIRMED tier (highest possible confidence).

SUPPORTED_SPORTS = ['MLB', 'NFL', 'NCAAF', 'NCAAB', 'NHL', 'UFC']


def fetch_flags(sport: str, since_hours: int = 24) -> list:
    """Pull unclassified (or recently re-fired) line_movement_flags for sport."""
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat().replace('+', '%2B')
    r = requests.get(
        f'{SB}/rest/v1/line_movement_flags'
        f'?sport=eq.{sport}&first_seen_at=gte.{since}'
        f'&select=id,game_id,market,side,pattern,detail,first_seen_at,classification',
        headers=H_READ, timeout=20)
    if r.status_code != 200:
        print(f'  ✗ flags fetch {r.status_code}')
        return []
    return r.json() or []


def _fetch_oddscrowd_split(game_id: str, market: str) -> dict | None:
    """Return latest oddscrowd snapshot for (game, market)."""
    r = requests.get(
        f'{SB}/rest/v1/line_snapshot'
        f'?game_id=eq.{game_id}&market=eq.{market}&source=eq.oddscrowd'
        f'&order=snapshot_ts.desc&limit=1',
        headers=H_READ, timeout=15)
    if r.status_code != 200:
        return None
    rows = r.json() or []
    return rows[0] if rows else None


def _fetch_fadereport_split(game_id: str, market: str) -> dict | None:
    """Return latest fadereport snapshot for (game, market). Returns None if
    table doesn't exist yet (migration 20260814_fadereport_signals pending)."""
    r = requests.get(
        f'{SB}/rest/v1/fadereport_signals'
        f'?game_id=eq.{game_id}&market=eq.{market}'
        f'&order=fetched_at.desc&limit=1',
        headers=H_READ, timeout=10)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        return None
    rows = r.json() or []
    return rows[0] if rows else None


def _fetch_cleatz_split(game_id: str, market: str) -> dict | None:
    """Return latest cleatz snapshot for (game, market). 3rd public-splits
    source added 2026-08-15. Table has sharp_side_norm + sharp/other
    bets% + handle% + divergence."""
    r = requests.get(
        f'{SB}/rest/v1/cleatz_signals'
        f'?game_id=eq.{game_id}&market=eq.{market}'
        f'&order=fetched_at.desc&limit=1',
        headers=H_READ, timeout=10)
    if r.status_code == 404 or r.status_code != 200:
        return None
    rows = r.json() or []
    return rows[0] if rows else None


def _cleatz_split_on_side(cleatz: dict | None, side: str) -> tuple:
    """Cleatz schema differs — sharp side has money_side_pct/bets_side_pct
    ON the sharp side, other has the other side. Returns (money%, bets%)
    for the requested side after flipping if needed."""
    if not cleatz: return (None, None)
    sharp_norm = (cleatz.get('sharp_side_norm') or '').upper()
    side_up = (side or '').upper()
    sharp_money = cleatz.get('sharp_handle_pct')
    sharp_bets = cleatz.get('sharp_bets_pct')
    other_money = cleatz.get('other_handle_pct')
    other_bets = cleatz.get('other_bets_pct')
    if sharp_money is None or sharp_bets is None:
        return (None, None)
    same_side_pairs = {('HOME','HOME'), ('AWAY','AWAY'),
                       ('OVER','OVER'), ('UNDER','UNDER')}
    if (sharp_norm, side_up) in same_side_pairs:
        return (float(sharp_money), float(sharp_bets))
    if other_money is None or other_bets is None:
        return (None, None)
    return (float(other_money), float(other_bets))


def _split_on_side(snap: dict | None, side: str, key_money: str, key_bets: str) -> tuple:
    """Return (money_pct, bets_pct) ON `side` from a snapshot row.

    Snapshot's pick_side is the side the snapshot's percentages describe.
    If pick_side matches side → percentages are ON side; if opposite → invert.
    """
    if not snap:
        return (None, None)
    money = snap.get(key_money); bets = snap.get(key_bets)
    if money is None or bets is None:
        return (None, None)
    pick = (snap.get('pick_side') or '').upper()
    side_up = (side or '').upper()
    # Normalize direction words
    same_side_pairs = {
        ('HOME', 'HOME'), ('AWAY', 'AWAY'),
        ('OVER', 'OVER'), ('UNDER', 'UNDER'),
    }
    if (pick, side_up) in same_side_pairs:
        return (float(money), float(bets))
    return (100.0 - float(money), 100.0 - float(bets))


def _fadereport_split_on_side(fr: dict | None, side: str) -> tuple:
    """FR-specific replacement for _split_on_side.

    2026-08-16 morning-audit bug: _split_on_side reads snap.get('pick_side'),
    but Fadereport rows have no `pick_side` field — they use `sharp_side_norm`.
    That silently defaulted `pick=''`, missed the same_side_pairs check, and
    inverted every FR read whenever sharp_side == flag side. Result: 8/15 had
    zero SHARP_TRIPLE_CONFIRMED despite the CZ/FR/OC agreement being present.

    Mirrors _cleatz_split_on_side: use sharp_side_norm to decide whether the
    snapshot's percentages describe `side` directly or the opposite.
    """
    if not fr:
        return (None, None)
    money = fr.get('money_side_pct')
    bets = fr.get('bets_side_pct')
    if money is None or bets is None:
        return (None, None)
    sharp_norm = (fr.get('sharp_side_norm') or fr.get('pick_side') or '').upper()
    side_up = (side or '').upper()
    same_side_pairs = {('HOME', 'HOME'), ('AWAY', 'AWAY'),
                       ('OVER', 'OVER'), ('UNDER', 'UNDER')}
    if (sharp_norm, side_up) in same_side_pairs:
        return (float(money), float(bets))
    return (100.0 - float(money), 100.0 - float(bets))


def classify_flag(sport: str, flag: dict) -> dict | None:
    """Classify one flag. Returns payload dict for PATCH, or None if skip."""
    gid = flag['game_id']; market = flag['market']; side = flag['side']
    pattern = (flag.get('pattern') or '').lower()

    oc = _fetch_oddscrowd_split(gid, market)
    fr = _fetch_fadereport_split(gid, market)
    cz = _fetch_cleatz_split(gid, market)

    oc_money, oc_bets = _split_on_side(oc, side, 'money_pct', 'bets_pct')
    # 2026-08-16 morning-audit fix: use FR-specific reader that respects
    # sharp_side_norm (FR has no pick_side field). Was silently inverting
    # every FR read where sharp_side == flag side → zero TRIPLE_CONFIRMED
    # on 8/15 despite CZ/OC/FR agreement being present.
    fr_handle, fr_bettors = _fadereport_split_on_side(fr, side)
    cz_money, cz_bets = _cleatz_split_on_side(cz, side)

    # RLM: line moved AWAY from the side that had heavy public money.
    # Other patterns (steam/limit): line moved TOWARD the side listed.
    line_moved_toward = pattern != 'rlm'

    # Classify from each source independently.
    oc_cls = classify_split(sport, oc_money, oc_bets, line_moved_toward) \
             if oc_money is not None else None
    fr_cls = classify_split(sport, fr_handle, fr_bettors, line_moved_toward) \
             if fr_handle is not None else None
    cz_cls = classify_split(sport, cz_money, cz_bets, line_moved_toward) \
             if cz_money is not None else None

    # 3-SOURCE COMBINATION (added 2026-08-15 pm; 2026-08-16 dropped OC-anchor)
    # Highest tier: SHARP_TRIPLE_CONFIRMED when all 3 sources agree on a
    # loud tag — regardless of which source. Previously required OC to be
    # loud, which silenced CZ+FR agreement when OC was NEUTRAL.
    loud_tags = {'SHARP_MOVE', 'PUBLIC_MOVE', 'RLM', 'CONSENSUS'}
    three_agree = (oc_cls in loud_tags and oc_cls == fr_cls == cz_cls)
    if three_agree:
        classification = oc_cls + '_TRIPLE_CONFIRMED'
    else:
        # 2-source fallback: try all three pairings (OC+FR, OC+CZ, FR+CZ)
        # and take the strongest resulting classification. Previously only
        # considered OC-anchored pairs, silencing FR+CZ agreement when OC
        # was NEUTRAL. Ranking done by combine_classifications: CONFIRMED
        # > SOURCES_SPLIT > LEAN > PATTERN_ONLY.
        candidates = []
        for a, b in ((oc_cls, fr_cls), (oc_cls, cz_cls), (fr_cls, cz_cls)):
            if a is None and b is None: continue
            c, _ = combine_classifications(a, b)
            candidates.append(c)
        # Prefer CONFIRMED > SPLIT > LEAN > anything else
        def _rank(c: str) -> int:
            if c.endswith('_CONFIRMED'): return 0
            if c == 'SOURCES_SPLIT': return 1
            if c.endswith('_LEAN'): return 2
            return 3
        classification = min(candidates, key=_rank) if candidates else 'PATTERN_ONLY'

    # 2026-08-16 morning-audit gate: single-source SHARP_MOVE_LEAN went
    # 1-2 on 8/15, and every LEAN flag on the slate had money% < 60.
    # Sub-60 single-source "sharp" is noise — demote to PATTERN_ONLY so
    # it does not surface a loud SHARP badge in the app.
    # Publication requires EITHER money% >= 60 OR |money% - bets%| >= 25pp.
    if classification.endswith('_LEAN'):
        # The winning source is whichever one produced the LEAN tag
        winning_money = oc_money if oc_cls == classification.rsplit('_', 1)[0] + '_LEAN' or oc_cls else None
        # Fall back: try OC first, then FR (handle), then CZ
        chk_money = oc_money if oc_money is not None else (fr_handle if fr_handle is not None else cz_money)
        chk_bets = oc_bets if oc_bets is not None else (fr_bettors if fr_bettors is not None else cz_bets)
        magnitude_ok = chk_money is not None and chk_money >= 60
        delta_ok = (chk_money is not None and chk_bets is not None
                    and abs(chk_money - chk_bets) >= 25)
        if not (magnitude_ok or delta_ok):
            classification = 'PATTERN_ONLY'

    # 2026-08-17 morning-audit gate: SOURCES_SPLIT went 1-2 (33%) on 8/16,
    # 3-2 (60%) on 8/15 — n small but consistently at-or-under breakeven
    # across two days. Demote to PATTERN_ONLY so the app doesn't render
    # a loud SOURCES DISAGREE badge that reads as actionable. Pattern
    # still flagged in the row for internal audit; just not published.
    if classification == 'SOURCES_SPLIT':
        classification = 'PATTERN_ONLY'

    # Only surface split numbers when they carry weight: CONFIRMED/TRIPLE
    # shows both sources (they agreed); LEAN shows whichever source spoke;
    # SPLIT shows both so user sees the disagreement; PATTERN_ONLY/NEUTRAL
    # don't surface any numbers (would mislead).
    show_numbers = ('_CONFIRMED' in classification or
                    '_TRIPLE_CONFIRMED' in classification or
                    classification.endswith('_LEAN') or
                    classification == 'SOURCES_SPLIT')

    # 2026-08-20: rewrite the flag's `detail` text to match the final
    # classification. detect_line_movement writes detail at detect-time
    # from OC-only data ("Money% 51 vs bets% 35 — sharp $ on away"). When
    # classify then flips to PUBLIC_MOVE_CONFIRMED using FR+CZ, the stale
    # detect-time text contradicts the classification — this bit us on
    # OAK/KC 8/18 where users saw "sharp $ on away" alongside a badge
    # meaning "public on away". Now the detail is rebuilt from the
    # classification + side + strongest source's numbers.
    matchup, market_label = _parse_matchup_market(flag.get('detail') or '')
    inv = {'HOME': 'AWAY', 'AWAY': 'HOME', 'OVER': 'UNDER', 'UNDER': 'OVER'}
    side_up = str(side or '').upper()
    other_side = inv.get(side_up, side_up)
    prefix = f'{matchup} · {market_label}: ' if matchup else ''

    if '_TRIPLE_CONFIRMED' in classification:
        base = classification.replace('_TRIPLE_CONFIRMED', '')
        if base == 'SHARP_MOVE':
            new_detail = f'{prefix}Sharps on {side_up} — all 3 sources confirm (FR + CZ + OC)'
        elif base == 'PUBLIC_MOVE':
            new_detail = f'{prefix}Public stacked on {side_up} — sharp side is {other_side} (all 3 sources)'
        elif base == 'RLM':
            new_detail = f'{prefix}RLM: line moved off {side_up} while public sits on it — sharp signal (all 3)'
        elif base == 'CONSENSUS':
            new_detail = f'{prefix}Sharps + public both on {side_up} — heavy-agreement play (all 3 sources)'
        else:
            new_detail = flag.get('detail') or ''
    elif '_CONFIRMED' in classification:
        base = classification.replace('_CONFIRMED', '')
        if base == 'SHARP_MOVE':
            new_detail = f'{prefix}Sharps on {side_up} — 2 of 3 sources confirm'
        elif base == 'PUBLIC_MOVE':
            new_detail = f'{prefix}Public stacked on {side_up} — sharp side is {other_side} (2 of 3 sources)'
        elif base == 'RLM':
            new_detail = f'{prefix}RLM: line moved off {side_up} while public sits on it — sharp signal (2 of 3)'
        elif base == 'CONSENSUS':
            new_detail = f'{prefix}Sharps + public both on {side_up} — heavy agreement (2 of 3 sources)'
        else:
            new_detail = flag.get('detail') or ''
    elif classification.endswith('_LEAN'):
        base = classification.replace('_LEAN', '')
        if base == 'SHARP_MOVE':
            new_detail = f'{prefix}Sharp lean on {side_up} — single-source read'
        elif base == 'PUBLIC_MOVE':
            new_detail = f'{prefix}Public leaning {side_up} — sharp side is {other_side} (single-source)'
        elif base == 'RLM':
            new_detail = f'{prefix}RLM lean: line off {side_up} while public sits — single-source'
        elif base == 'CONSENSUS':
            new_detail = f'{prefix}Sharps + public both leaning {side_up} — single-source'
        else:
            new_detail = flag.get('detail') or ''
    elif classification == 'SOURCES_SPLIT':
        new_detail = f'{prefix}Sources disagree on {market_label or "this market"} — mixed signal, no clear side'
    else:
        # PATTERN_ONLY / NEUTRAL — keep original detail (it's just the raw
        # pattern text like "2 books shifted toward away within 0 min").
        new_detail = flag.get('detail') or ''

    payload = {
        'classification': classification,
        'detail':        new_detail[:400],  # cap length; matches DB column bounds
        'money_pct':     round(oc_money, 1) if show_numbers and oc_money is not None else None,
        'bets_pct':      round(oc_bets, 1)  if show_numbers and oc_bets  is not None else None,
        'handle_pct':    round(fr_handle, 1)   if show_numbers and fr_handle   is not None else None,
        'bettors_pct':   round(fr_bettors, 1)  if show_numbers and fr_bettors  is not None else None,
        'classified_at': datetime.now(timezone.utc).isoformat(),
    }
    return payload


def _parse_matchup_market(detail: str) -> tuple:
    """Extract '{matchup} · {market_label}' prefix from detect-time detail.

    detect_line_movement writes detail like
      'Toronto Blue Jays @ Tampa Bay Rays · run line: Money% 51 vs bets% 35 — sharp $ on away'
    We split on ' · ' and then on ': ' to recover the matchup + market label
    so classify can rebuild the tail with accurate wording. If parsing fails
    (older flags or unusual formats), returns ('', '') and downstream falls
    back to the original detail text.

    2026-08-22: historic flags may have the literal string 'game' as the
    matchup prefix (fallback when matchup_by_gid didn't cover the game_id).
    Treat 'game' as no-matchup so classify rebuilds without the placeholder
    and downstream frontends look up the matchup another way.
    """
    if not detail or ' · ' not in detail:
        return ('', '')
    try:
        prefix, _rest = detail.split(': ', 1)
    except ValueError:
        return ('', '')
    parts = prefix.split(' · ', 1)
    if len(parts) != 2:
        return ('', '')
    matchup, market_label = parts[0].strip(), parts[1].strip()
    if matchup.lower() == 'game':
        matchup = ''
    return (matchup, market_label)


def patch_flag(flag_id: int, payload: dict) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/line_movement_flags?id=eq.{flag_id}',
        headers=H_WRITE, json=payload, timeout=15)
    return r.status_code in (200, 204)


def run_sport(sport: str, dry_run: bool = False) -> tuple:
    flags = fetch_flags(sport)
    print(f'  {sport}: {len(flags)} recent flags')
    if not flags:
        return (0, 0)

    counts = {}
    written = 0
    for flag in flags:
        payload = classify_flag(sport, flag)
        if not payload:
            continue
        cls = payload['classification']
        counts[cls] = counts.get(cls, 0) + 1
        if dry_run:
            print(f'    [DRY] flag#{flag["id"]:>4} {flag["market"]:<8} {flag["side"]:<6} '
                  f'{flag["pattern"]:<7} → {cls:<12} · '
                  f'money%={payload["money_pct"]} bets%={payload["bets_pct"]}')
            written += 1
            continue
        if patch_flag(flag['id'], payload):
            written += 1

    print(f'  {sport}: classifications → ' +
          ', '.join(f'{k}={v}' for k, v in counts.items() if v > 0))
    return (written, len(flags))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=SUPPORTED_SPORTS + ['ALL'], default='ALL')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    sports = SUPPORTED_SPORTS if args.sport == 'ALL' else [args.sport]
    print(f'=== classify_line_moves · {"/".join(sports)} '
          f'{"[DRY]" if args.dry_run else ""} ===')
    total_written = total_flags = 0
    for s in sports:
        w, n = run_sport(s, dry_run=args.dry_run)
        total_written += w; total_flags += n
    print(f'\n  ✓ {total_written}/{total_flags} flags classified')


if __name__ == '__main__':
    main()
