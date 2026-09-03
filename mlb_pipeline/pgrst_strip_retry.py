"""Shared PGRST204 strip-retry helper.

Pattern extracted from nfl_game_context.py after we discovered a single
missing column silently zeroed the entire NFL context slate for weeks.
When PostgREST responds 400 with a missing-column message, this helper
parses the column out of the error, strips it from every row, and retries
the POST — up to `max_iter` times so schema-lag migrations don't kill
the whole pipeline.

Usage:
    from pgrst_strip_retry import post_with_strip_retry
    r, stripped = post_with_strip_retry(url, headers, rows)
    if stripped and r.status_code in (200, 201, 204):
        print(f'  ⚠ stripped missing cols ({stripped[:5]}) — schema lag')
"""
from __future__ import annotations

import re
from typing import Any

import requests

_MISSING_COL_RE = re.compile(r"'([a-z_0-9]+)'")


def post_with_strip_retry(
    url: str,
    headers: dict,
    rows: list[dict],
    timeout: int = 30,
    max_iter: int = 60,
) -> tuple[requests.Response, list[str]]:
    """POST rows to PostgREST; on missing-column 400, strip + retry.

    Returns (final_response, list_of_stripped_columns).
    Caller is responsible for logging + status handling.
    """
    r = requests.post(url, headers=headers, json=rows, timeout=timeout)
    stripped: list[str] = []
    if r.status_code != 400:
        return r, stripped

    for _ in range(max_iter):
        try:
            err = r.json() if r.headers.get('content-type','').startswith('application/json') else {}
            msg = err.get('message', '') if isinstance(err, dict) else ''
        except (ValueError, AttributeError):
            msg = r.text
        m = _MISSING_COL_RE.search(msg)
        if not m or r.status_code != 400:
            break
        col = m.group(1)
        if not any(col in row for row in rows):
            break
        for row in rows:
            row.pop(col, None)
        stripped.append(col)
        r = requests.post(url, headers=headers, json=rows, timeout=timeout)
        if r.status_code in (200, 201, 204):
            break
    return r, stripped
