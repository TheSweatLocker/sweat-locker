"""sb_observer - make every non-2xx LOUD, at the one place they all pass.

== WHY THIS IS NOT 386 EDITS (2026-11-04) ==================================
B152/B153/B154. Andy: "lets continue with backlog fixes, root cause solution."

The pattern the backlog names is real and it is everywhere:

    if r.status_code != 200: return []          80 sites
    r.json() if r.status_code == 200 else []   306 sites
                                               ---------
                                               386 call sites

A 400 is a bug in our code, not a quiet day - but every one of those sites
turns one into an empty list, and the step exits green. It is the fifth
occurrence of that class and it has cost days of dark data (the scrape that
wrote nothing for 39 days, align_status 0 of 310, the NCAAB panel that could
never fire).

Editing 386 call sites is the wrong fix twice over: it is 386 chances to
introduce a new bug, and it does not cover the 387th site somebody writes
next week. Every one of those calls goes through `requests`, so that is the
chokepoint - the same lesson as feedback_guard_belongs_at_the_chokepoint:
a guard on 1 source of 8 is not a guard.

So this observes `requests.sessions.Session.request` and reports. It
DELIBERATELY DOES NOT CHANGE CONTROL FLOW: no raising, no retrying, no
altering the response. A caller that swallows still swallows - it just can
no longer do it silently. That keeps the blast radius at zero while making
the whole class visible, which is the only safe way to land this across a
175-step pipeline in one go.

Opt in to failure separately with SB_OBSERVER_STRICT=1, which exits non-zero
at the end of a run that saw a schema or constraint error. That is for CI,
not for the nightly, so a loud run and a failed run stay different things.

== WHAT IT CLASSIFIES =====================================================
A PostgREST error body already names the defect precisely, which is why this
beats a hand-maintained column whitelist (B152's original shape): 42703
tells you WHICH column does not exist, with no list to keep in sync.

    42703  undefined column   <- our select/filter names a column that is gone
    42P01  undefined table
    42P10  bad ON CONFLICT target
    23502  NOT NULL violation  <- today's POTD swap hit exactly this
    23505  duplicate key       <- merge-duplicates without on_conflict
    57014  statement timeout   <- the query is too big, not wrong
    PGRST* PostgREST level (relationship, schema cache, parse)

SECRETS NEVER PRINT. The apikey and Authorization token appear in these
requests; query values that look key-shaped are redacted and headers are
never echoed.
"""
from __future__ import annotations
import atexit
import os
import re
import sys
import threading
from collections import Counter

_INSTALLED = False
_LOCK = threading.Lock()
_STATS: Counter = Counter()
_SEEN: dict = {}
_FIRST: list = []

_MAX_REPORTS = int(os.environ.get('SB_OBSERVER_MAX_REPORTS', '40'))
_STRICT = os.environ.get('SB_OBSERVER_STRICT', '') == '1'
_QUIET = os.environ.get('SB_OBSERVER_QUIET', '') == '1'

# A schema error is OUR bug and can never be a transient. Kept apart from
# 5xx and timeouts, which are the network having a bad day.
_SCHEMA_CODES = {'42703', '42P01', '42P10', '42883', '42601'}
_CONSTRAINT_CODES = {'23502', '23505', '23503', '23514'}

_KEYISH = re.compile(r'(apikey|key|token|secret|password|authorization)=[^&]*',
                     re.I)


def _safe_url(url):
    """URL with anything key-shaped redacted and the host trimmed off."""
    u = _KEYISH.sub(lambda m: m.group(0).split('=')[0] + '=<redacted>',
                    str(url or ''))
    # the supabase host is on every line and identifies nothing; the path does
    return re.sub(r'^https?://[^/]+/rest/v1/', '', u)


def _parse_pg(body):
    """(code, message) from a PostgREST error body, without a json parse."""
    if not body:
        return None, None
    code = re.search(r'"code"\s*:\s*"([^"]+)"', body)
    msg = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', body)
    return (code.group(1) if code else None,
            msg.group(1) if msg else None)


def _classify(code):
    if code in _SCHEMA_CODES:
        return 'SCHEMA'
    if code in _CONSTRAINT_CODES:
        return 'CONSTRAINT'
    if code == '57014':
        return 'TIMEOUT'
    if str(code or '').startswith('PGRST'):
        return 'PGRST'
    return 'HTTP'


def _report(method, status, url, body):
    code, msg = _parse_pg(body)
    kind = _classify(code)
    with _LOCK:
        _STATS['status_%d' % status] += 1
        _STATS['kind_' + kind] += 1
        if code:
            _STATS['code_' + code] += 1
        key = (method, status, code, _safe_url(url).split('?')[0])
        _SEEN[key] = _SEEN.get(key, 0) + 1
        n = _SEEN[key]
        total = sum(_SEEN.values())
        if kind in ('SCHEMA', 'CONSTRAINT') and len(_FIRST) < 12:
            entry = '%s %s %s %s' % (kind, code, method,
                                     _safe_url(url).split('?')[0])
            if msg:
                entry += ' - ' + str(msg)[:90]
            if entry not in _FIRST:
                _FIRST.append(entry)
    if _QUIET or n > 1 or total > _MAX_REPORTS:
        return                      # first occurrence of each shape only
    detail = ('%s: %s' % (code, str(msg)[:160])) if code else str(body)[:160]
    print('  !! %s %s %s %s\n     %s'
          % (kind, status, method, _safe_url(url)[:120], detail),
          file=sys.stderr, flush=True)


def _summary():
    with _LOCK:
        bad = dict((k, v) for k, v in _STATS.items()
                   if k.startswith('status_'))
        if not bad:
            return
        schema = _STATS.get('kind_SCHEMA', 0)
        constraint = _STATS.get('kind_CONSTRAINT', 0)
        ok = _STATS.get('ok', 0)
        codes = dict((k[5:], v) for k, v in _STATS.items()
                     if k.startswith('code_'))
        first = list(_FIRST)
    total_bad = sum(bad.values())
    rate = (100.0 * total_bad / (ok + total_bad)) if (ok + total_bad) else 0.0
    out = sys.stderr
    print('\n  == sb_observer: %d non-2xx of %d requests (%.1f%%) =='
          % (total_bad, ok + total_bad, rate), file=out)
    for k in sorted(bad):
        print('     %5s  x%d' % (k[7:], bad[k]), file=out)
    if codes:
        print('     pg codes: '
              + ', '.join('%s x%d' % (c, n) for c, n in sorted(codes.items())),
              file=out)
    if first:
        print('     OUR BUGS (schema/constraint - never transient):', file=out)
        for f in first:
            print('       - ' + f, file=out)
    if _STRICT and (schema or constraint):
        print('     SB_OBSERVER_STRICT: failing run on %d schema + %d '
              'constraint error(s)' % (schema, constraint), file=out)
        out.flush()
        os._exit(3)


def stats():
    """Current counters, for a caller that wants to assert on them."""
    with _LOCK:
        return dict(_STATS)


def install():
    """Wrap Session.request once. Safe to call repeatedly."""
    global _INSTALLED
    if _INSTALLED:
        return False
    try:
        import requests.sessions as _rs
    except Exception:
        return False
    _orig = _rs.Session.request

    def _wrapped(self, method, url, *a, **kw):
        resp = _orig(self, method, url, *a, **kw)
        try:
            sc = getattr(resp, 'status_code', None)
            if isinstance(sc, int):
                if 200 <= sc < 300:
                    with _LOCK:
                        _STATS['ok'] += 1
                else:
                    try:
                        body = resp.text[:600]
                    except Exception:
                        body = ''
                    _report(str(method).upper(), sc, url, body)
        except Exception:
            pass            # an observer must never be the thing that breaks
        return resp

    _rs.Session.request = _wrapped
    _wrapped._sb_observer = True
    _INSTALLED = True
    atexit.register(_summary)
    return True


install()
