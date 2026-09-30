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

# ════════════════════════════════════════════════════════════════════════
# PROVIDER SEAM — 2026-09-29
#
# Andy, after the API-key abuse incident: "How fast could we get another
# provider wired up and would it need a client hardcode change or is it all
# back end? I want to wait so i avoid having to pay another provider."
#
# So: the seam is built, the switch defaults OFF, and no second account is
# needed until the day it is flipped. Nothing about today's behaviour changes.
#
# WHY IT COSTS NOTHING TO CARRY. Every generator in this repo sends the same
# trivial shape — one user message, a model, a max_tokens. Audited 2026-09-29
# across all 12: not one uses `system`, `tools`, `temperature` or multi-turn.
# So a provider swap is a URL, a header, a body key and a response path. There
# is nothing to design later.
#
# HOW TO ACTUALLY FLIP IT (backend only, no app build, no App Store review):
#     LLM_PROVIDER=openai_compatible
#     LLM_API_KEY=<key>
#     LLM_MODEL=<that provider's model id>
#     LLM_BASE_URL=<optional, defaults to OpenAI>
# Set those as GitHub Actions secrets and the pipeline switches on next run.
# The app switches separately via the claude-proxy edge function, which
# translates in and out of the Anthropic shape so the client never knows.
#
# WHY 'openai_compatible' RATHER THAN A NAMED VENDOR. OpenAI, Groq, Together,
# DeepSeek, Mistral and OpenRouter all speak /chat/completions with the same
# body and the same choices[0].message.content response. One adapter covers the
# whole market, so the cheapest option on the day wins instead of this choice
# being made now, in advance, by me.
#
# The caller's `model` argument is an Anthropic id, so it is IGNORED when a
# non-Anthropic provider is active — LLM_MODEL supplies the real one. That is
# deliberate: it means none of the 12 call sites need touching to switch.
# ════════════════════════════════════════════════════════════════════════

OPENAI_DEFAULT_URL = 'https://api.openai.com/v1/chat/completions'


def active_provider() -> str:
    """Which provider this process will use. 'anthropic' unless overridden."""
    return (os.environ.get('LLM_PROVIDER') or 'anthropic').strip().lower()


def _provider_key(provider: str) -> Optional[str]:
    if provider == 'anthropic':
        return os.environ.get('ANTHROPIC_API_KEY')
    return os.environ.get('LLM_API_KEY') or os.environ.get('OPENAI_API_KEY')


def _build_request(provider: str, prompt: str, model: str,
                   max_tokens: int, key: str):
    """Return (url, headers, json_body) for the active provider."""
    if provider == 'anthropic':
        return (
            API_URL,
            {'Content-Type': 'application/json', 'x-api-key': key,
             'anthropic-version': API_VERSION},
            {'model': model, 'max_tokens': max_tokens,
             'messages': [{'role': 'user', 'content': prompt}]},
        )
    # openai_compatible
    return (
        os.environ.get('LLM_BASE_URL') or OPENAI_DEFAULT_URL,
        {'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'},
        {'model': os.environ.get('LLM_MODEL') or model,
         'max_tokens': max_tokens,
         'messages': [{'role': 'user', 'content': prompt}]},
    )


def _extract_text(provider: str, data: dict) -> str:
    """Pull the assistant text out of whichever response shape came back."""
    if provider == 'anthropic':
        return ''.join(b.get('text', '')
                       for b in (data.get('content') or [])
                       if b.get('type') == 'text').strip()
    choices = data.get('choices') or []
    if not choices:
        return ''
    return ((choices[0].get('message') or {}).get('content') or '').strip()


# Substrings that mean "this will not get better on its own". Matched against
# the response body because the HTTP status alone does not separate them:
# a low balance and a malformed prompt are both 400.
#
# 2026-09-29: the second group covers OpenAI-compatible providers. A fatal that
# is not recognised as fatal is the exact failure this module exists to stop —
# it would retry four times and then skip the game silently — so the markers
# have to cover whatever provider is actually active, not just Anthropic.
FATAL_MARKERS = (
    # Anthropic
    'credit balance is too low',
    'billing',
    'invalid x-api-key',
    'authentication_error',
    'permission_error',
    'account is not authorized',
    # OpenAI-compatible
    'insufficient_quota',
    'exceeded your current quota',
    'invalid_api_key',
    'incorrect api key',
    'account_deactivated',
    'billing_hard_limit_reached',
)
FATAL_STATUS = (401, 403)

MAX_ATTEMPTS = 4
BASE_SLEEP = 2.0

# Set once a non-default provider has been announced, so the notice appears
# per process rather than per generated read.
_announced = False


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
    provider = active_provider()
    key = api_key or _provider_key(provider)
    if not key:
        want = ('ANTHROPIC_API_KEY' if provider == 'anthropic' else 'LLM_API_KEY')
        raise FatalLLMError(f'{want} missing — nothing can be generated '
                            f'(LLM_PROVIDER={provider})')

    url, headers, payload = _build_request(provider, prompt, model,
                                           max_tokens, key)
    # Announce a non-default provider ONCE per process. A silent provider swap
    # is how you end up debugging the wrong vendor's rate limits.
    global _announced
    if provider != 'anthropic' and not _announced:
        print(f'  ℹ LLM provider = {provider} '
              f'(model {payload.get("model")}) — not Anthropic')
        _announced = True

    tag = f' [{label}]' if label else ''
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = requests.post(url, headers=headers, json=payload,
                              timeout=timeout)
            body = r.text or ''
            if r.status_code == 200:
                txt = _extract_text(provider, r.json())
                return txt or None

            if _is_fatal(r.status_code, body):
                raise FatalLLMError(
                    f'{provider} {r.status_code}{tag}: {body[:200]}')

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
