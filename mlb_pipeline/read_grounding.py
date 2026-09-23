"""Does every number in a generated read exist in the data it was given?

Built 2026-09-23 after the CHW @ KC card shipped four fabrications in one
paragraph: it named the wrong pitcher for an xERA, labelled a bullpen
save percentage as a barrel rate, wrote a model total that appears in no
field, and inverted `wind_blowing_in` to argue for its own pick.

None of those are intelligence failures. They are what happens when a
model is allowed to type numbers into prose and nothing checks the prose
against the snapshot that produced it. This module is that check.

Two passes, deliberately separated because they have very different
false-positive profiles:

  VALUE   every numeric token in the prose must appear in the snapshot,
          allowing for the rounding a writer legitimately does (4.15 ->
          "4.2", 3.1 -> "3.10"). High confidence, low noise.

  ATTRIB  a number appearing near a player's name must belong to THAT
          player's subtree. This is the check that catches "Lugo 3.1
          xERA" when 3.1 is Hudson's. Noisier - prose legitimately says
          "Hudson ... against Lugo's 5.09" - so it is reported
          separately and never folded into the headline rate.

Nothing here writes. It reads published rows and reports.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

# Numbers a writer produces from language, not from data. Matching these
# against the snapshot would flag every read that says "last three
# outings" or "a three-game win streak".
_PROSE_NUMBERS = {
    0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0,
    11.0, 12.0, 100.0,
}

# Baseball prose is full of leading-decimal rates (".301 average") and
# hyphenated compounds ("sub-9.5 total"). A naive [-+]?\d+(\.\d+)? reads
# the first as 301 and the second as -9.5, then reports both as
# fabrications. The lookbehind refuses a sign or digit-run that is glued
# to a word character, and \.\d+ is matched in its own right.
_TOKEN = re.compile(r'(?<![A-Za-z0-9])[-+]?(?:\d+\.\d+|\.\d+|\d+)')

# "1st", "2nd", "L5", "L14", "W3", "6-4", "2026" — structural, not claims
_SKIP_CONTEXT = re.compile(
    r'(?:\b[LW]\d+\b'                 # streak notation
    r'|\b\d+(?:st|nd|rd|th)\b'        # ordinals
    r'|\b\d+-\d+\b'                   # records / scores
    r'|\b20\d\d\b'                    # years
    r')')


def _walk(obj: Any, path: str = '') -> Iterable[tuple[str, Any]]:
    """Every leaf in a nested structure, with its dotted path."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f'{path}.{k}' if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f'{path}[{i}]')
    else:
        yield path, obj


def snapshot_values(snap: dict) -> dict[float, list[str]]:
    """Numeric leaves of the snapshot, value -> the paths holding it.

    Percentages are indexed both ways: a field storing 0.55 backs prose
    saying "55%", and one storing 23.1 backs "23.1%". Without this the
    checker flags every percentage in every read.
    """
    out: dict[float, list[str]] = {}

    def add(v: float, path: str):
        out.setdefault(round(float(v), 4), []).append(path)

    for path, v in _walk(snap):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            add(v, path)
            if 0.0 < abs(v) <= 1.0:
                add(round(v * 100, 4), path + '→%')
        elif isinstance(v, str):
            # numbers embedded in pre-chewed fact strings the prompt ships
            for m in _TOKEN.findall(v):
                try:
                    add(float(m), path)
                except ValueError:
                    pass
    return out


def _matches(claim: float, index: dict[float, list[str]]) -> list[str]:
    """Paths that support this claimed number, allowing honest rounding.

    A read that writes "4.2" for a stored 4.15 is doing its job. One that
    writes "8.1" for a stored 7.98 is not. The rule: the claim must be
    what you get by rounding the stored value to the claim's own
    precision.
    """
    if claim in index:
        return index[claim]
    text = f'{claim:f}'.rstrip('0')
    decimals = len(text.split('.')[1]) if '.' in text else 0
    hits = []
    for value, paths in index.items():
        if round(value, decimals) == round(claim, decimals):
            hits.extend(paths)
    return hits


def check_values(prose: str, snap: dict) -> list[dict]:
    """Numeric claims in the prose with no support in the snapshot."""
    index = snapshot_values(snap)
    masked = _SKIP_CONTEXT.sub(' ', prose or '')
    bad = []
    seen = set()
    for m in _TOKEN.finditer(masked):
        raw = m.group(0)
        try:
            claim = round(float(raw), 4)
        except ValueError:
            continue
        if claim in _PROSE_NUMBERS or claim in seen:
            continue
        seen.add(claim)
        if not _matches(claim, index):
            start = max(0, m.start() - 55)
            bad.append({
                'value': raw,
                'context': masked[start:m.end() + 25].strip(),
            })
    return bad


def _names(snap: dict) -> dict[str, str]:
    """Player name -> the snapshot subtree that player's stats live in."""
    out = {}
    for side in ('away', 'home'):
        p = ((snap.get('pitchers') or {}).get(side) or {})
        if p.get('name'):
            out[p['name']] = f'pitchers.{side}'
    if out:
        return out
    # audit_read wraps snapshot and context together, so pitchers sits a
    # level down. Find it wherever it is rather than hard-coding depth.
    for path, v in _walk(snap):
        if path.endswith('.name') and '.pitchers.' in path:
            out[str(v)] = path.rsplit('.name', 1)[0]
    # context rows carry the starters as flat away_pitcher/home_pitcher
    for path, v in _walk(snap):
        if path.endswith(('away_pitcher', 'home_pitcher')) and isinstance(v, str) and v:
            side = 'away' if path.endswith('away_pitcher') else 'home'
            out.setdefault(v, f'{side}_pitcher_')
    return out


def check_attribution(prose: str, snap: dict) -> list[dict]:
    """Numbers sitting beside a name that belong to someone else.

    Catches "Lugo 3.1 xERA" where 3.1 is the other starter's. Only fires
    when the number is supported SOMEWHERE in the snapshot but not under
    the named player, and only when no other name intervenes — which
    keeps legitimate comparative prose ("Hudson's 3.10 against Lugo's
    5.09") from tripping it.
    """
    index = snapshot_values(snap)
    owners = _names(snap)
    if not owners:
        return []
    surnames = {n.split()[-1]: subtree for n, subtree in owners.items()}
    pattern = re.compile(
        r'\b(' + '|'.join(re.escape(s) for s in surnames) + r')\b')

    bad = []
    for m in _TOKEN.finditer(prose or ''):
        try:
            claim = round(float(m.group(0)), 4)
        except ValueError:
            continue
        if claim in _PROSE_NUMBERS:
            continue
        paths = _matches(claim, index)
        if not paths:
            continue                      # already a VALUE finding
        before = prose[max(0, m.start() - 70):m.start()]
        near = pattern.findall(before)
        if not near:
            continue
        subtree = surnames[near[-1]]
        if any(subtree in p for p in paths):
            continue
        # Only accuse when the number demonstrably belongs to the OTHER
        # starter. A figure living in some unrelated team-level field is
        # a labelling problem, not a misattribution, and saying so here
        # would bury the real finding in noise.
        rivals = [s for s in surnames.values() if s != subtree]
        if not any(r in p for p in paths for r in rivals):
            continue
        bad.append({
            'value': m.group(0),
            'attributed_to': near[-1],
            'actually': sorted((p for p in paths
                                if any(r in p for r in rivals)))[0],
            'context': prose[max(0, m.start() - 70):m.end() + 20].strip(),
        })
    return bad


def audit_read(row: dict, context: dict | None = None) -> dict:
    """Both passes over one jerry_reads row.

    `context` is the game's mlb_game_context row, and passing it is not
    optional in practice. 2026-09-23: 34 of 51 claims that looked
    unsupported by input_snapshot turned out to be exact matches in the
    context row - away_wrc_vs_opp_hand, home_first_inning_whip and
    others are cited accurately by reads that do not carry those fields
    in their stored snapshot. So input_snapshot is NOT a faithful record
    of the prompt, and auditing against it alone manufactures
    accusations. Checking against the union biases toward acquittal,
    which is the correct direction for a check that gates publishing.
    """
    snap = row.get('input_snapshot') or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except Exception:
            snap = {}
    if context:
        snap = {'_snapshot': snap, '_context': context}
    result = {
        'id': row.get('id'),
        'game_id': row.get('game_id'),
        'game_date': str(row.get('game_date'))[:10],
        'sport': row.get('sport'),
        'has_snapshot': bool(row.get('input_snapshot')),
        'value': [],
        'attrib': [],
    }
    if not snap:
        return result
    for field in ('short_read', 'long_read'):
        text = row.get(field)
        if not text:
            continue
        for f in check_values(text, snap):
            result['value'].append({**f, 'field': field})
        for f in check_attribution(text, snap):
            result['attrib'].append({**f, 'field': field})
    return result
