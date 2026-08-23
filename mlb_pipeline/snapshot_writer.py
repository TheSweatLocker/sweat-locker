"""Shared primary_play snapshot writer for every sport's game_context.

Extracted 2026-08-23 so all per-sport game_context modules
(mlb / nfl / ncaaf / ncaab / nba / nhl / ufc) can call one function
instead of copy-pasting the write logic. Parallels defensive_gates.py.

Prior state: only mlb_game_context.py had the snapshot write. NCAAF/NFL/
NCAAB/NBA game_contexts silently NEVER populated primary_play_snapshots,
so the append-only audit trail (Wave 1b) was MLB-only. As those sports'
seasons open, users would get zero snapshot history — no way to audit
signal impact per pick across time.

Usage:
    from snapshot_writer import write_primary_play_snapshot
    if r.status_code in (200, 201, 204):
        write_primary_play_snapshot(SUPABASE_URL, headers, sport='NFL',
                                     context=context)

Best-effort — snapshot write failures never block the publish.
"""
from __future__ import annotations
from typing import Any

import requests


def write_primary_play_snapshot(supabase_url: str, headers: dict,
                                  sport: str, context: dict) -> bool:
    """Write one snapshot row for the current publish. Returns True if
    written, False if skipped (missing pp / missing game_id / write failed).

    Silent on 404 (migration not applied); loud on other failures.
    """
    pp = context.get("primary_play") if isinstance(context, dict) else None
    if not isinstance(pp, dict) or not context.get("game_id"):
        return False
    snap = {
        "sport": sport,
        "game_date": context.get("game_date"),
        "game_id": context.get("game_id"),
        "snapshot_source": "card_lock",
        "home_team": context.get("home_team"),
        "away_team": context.get("away_team"),
        "primary_play": pp,
        "pick_type": pp.get("type"),
        "pick_label": pp.get("label"),
        "pick_side": pp.get("side"),
        "pick_line": pp.get("line"),
        "tier": pp.get("tier"),
        "conviction": pp.get("conviction"),
        "score": pp.get("score"),
    }
    try:
        r = requests.post(
            f"{supabase_url}/rest/v1/primary_play_snapshots",
            headers={**headers, "Prefer": "return=minimal"},
            json=snap, timeout=8,
        )
        if r.status_code >= 400 and r.status_code != 404:
            print(f"  ⚠ snapshot write {r.status_code}: {r.text[:80]}")
            return False
        return r.status_code < 300
    except Exception:
        return False  # never block publish
