"""Tell a broken API key from a busy one, and stop the run for the first.

── WHY ──
Andy 2026-09-28: "i think i was getting throttled by anthropgic calls."
Close, but the logs said something more actionable — 15 of these, at the tail
of a full NHL regeneration:

    ⚠ claude 400: 'Your credit balance is too low to access the Anthropic
     API. Please go to Plans & Billing to upgrade or purchase credits.'

A 400, not a 429. The account ran out of credits mid-run, and every generator
handles that the same way:

    if narrative is None:
        print(f'  ✗ {matchup}: claude failed')
        skipped += 1
        continue

so the run wrote 50 reads, silently abandoned 15, and EXITED 0. Nothing was
lost that day only because those games already had older reads. On a fresh
slate the same failure ships games with no analysis at all and still reports
success — the failure mode this repo keeps rediscovering.

── THE DISTINCTION THAT MATTERS ──
Two API failures look identical at the call site and could not be more
different in what you should do about them:

  FATAL — no key, bad key, no credit, permission denied. Every subsequent
    call in the run WILL fail the same way. Retrying is pure waste and
    continuing produces a green run with missing data. Stop immediately and
    exit non-zero so somebody is told.

  TRANSIENT — 429 rate limit, 429 with Retry-After, 5xx, overloaded, socket
    timeout. The next call may well succeed. These deserve a backoff, and
    throwing the work away instead is the expensive mistake: a read that cost
    a full prompt to build is discarded over a two-second wait.

Before this, both were one `return None`.

── DESIGN ──
`call()` returns the text, or raises FatalLLMError for the first class. The
caller is expected NOT to catch it — the whole point is that the run dies. A
transient failure still returns None after its retries so existing
skip-and-continue handling keeps working.

Retry-After is honoured when the API sends it, because a server that tells
you when to come back knows better than any local guess.
"""
from __future__ import annotations

import os
import random
import time
from typing import Optional

import requests

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'

# Substrings that mean "this will not get better on its own". Matched against
# the response body because the HTTP status alone does not separate them:
# a low balance and a malformed prompt are both 400.
FATAL_MARKERS = (
    'credit balance is too low',
    'billing',
    'invalid x-api-key',
    'authentication_error',
    'permission_error',
    'account is not authorized',
)
FATAL_STATUS = (401, 403)

MAX_ATTEMPTS = 4
BASE_SLEEP = 2.0


class FatalLLMError(RuntimeError):
    """Unrecoverable: key, credit or permission. Do not retry, do not continue."""


def _is_fatal(status: int, body: str) -> bool:
    if status in FATAL_STATUS:
        return True
    low = (body or '').lower()
    return any(m in low for m in FATAL_MARKERS)


def call(prompt: str, model: str, max_tokens: int = 800,
         timeout: int = 60, api_key: Optional[str] = None,
         label: str = '') -> Optional[str]:
    """Text on success, None on transient failure, FatalLLMError on fatal."""
    key = api_key or os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        raise FatalLLMError('ANTHROPIC_API_KEY missing — nothing can be generated')

    tag = f' [{label}]' if label else ''
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = requests.post(
                API_URL,
                headers={'Content-Type': 'application/json',
                         'x-api-key': key,
                         'anthropic-version': API_VERSION},
                json={'model': model, 'max_tokens': max_tokens,
                      'messages': [{'role': 'user', 'content': prompt}]},
                timeout=timeout)
            body = r.text or ''
            if r.status_code == 200:
                data = r.json()
                txt = ''.join(b.get('text', '')
                              for b in (data.get('content') or [])
                              if b.get('type') == 'text').strip()
                return txt or None

            if _is_fatal(r.status_code, body):
                raise FatalLLMError(
                    f'Anthropic {r.status_code}{tag}: {body[:200]}')

            # Transient. Prefer the server's own Retry-After over a guess.
            wait = BASE_SLEEP * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            ra = r.headers.get('retry-after')
            if ra:
                try:
                    wait = max(wait, float(ra))
                except (TypeError, ValueError):
                    pass
            if attempt == MAX_ATTEMPTS:
                print(f'  ⚠ claude {r.status_code}{tag} — giving up after '
                      f'{MAX_ATTEMPTS} attempts: {body[:140]}')
                return None
            print(f'  ⚠ claude {r.status_code}{tag} — retry {attempt}/'
                  f'{MAX_ATTEMPTS - 1} in {wait:.1f}s')
            time.sleep(wait)

        except FatalLLMError:
            raise
        except requests.RequestException as e:
            if attempt == MAX_ATTEMPTS:
                print(f'  ⚠ claude call failed{tag} after {MAX_ATTEMPTS}: {e}')
                return None
            wait = BASE_SLEEP * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            print(f'  ⚠ claude network error{tag} — retry {attempt} in {wait:.1f}s')
            time.sleep(wait)
    return None
