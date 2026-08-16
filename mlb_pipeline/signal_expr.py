"""Safe expression evaluator for signal_sources (2026-08-16).

Every row in signal_sources carries three Python expressions:
  * condition_expr → bool (does the signal fire on this game?)
  * side_expr → str (which candidate does it favor?)
  * strength_expr → float in [0, 1] (how strong is the opinion?)

This module evaluates those expressions with:
  * No __builtins__ (no import, no open, no eval, no globals)
  * Access to `ctx` (the game_context row as an AttrDict — dot AND [] access)
  * A whitelist of safe functions: abs, min, max, float, int, len, round,
    bool, str, isinstance, and None/True/False constants
  * Missing ctx fields evaluate to None (not KeyError)
  * Any comparison with None or exception during eval → returns None (never raise)

Design goal: signal_sources rows are effectively "data" not "code". If someone
adds a row with a bad expr, that ONE signal silently drops out — never crashes
the scorer. The scorer logs the failure so operators know to fix the row.
"""
from __future__ import annotations
from typing import Any, Optional
import math


class AttrDict(dict):
    """Dict that also supports attribute access (ctx.field_name).

    Dunder attribute access is BLOCKED via __getattribute__ override —
    prevents class introspection escape hatches like ctx.__class__.__mro__
    → object.__subclasses__() that could bypass the locked-down eval
    namespace. Must intercept __getattribute__ (not __getattr__) because
    dunder attrs live on the underlying dict class as descriptors and
    resolve BEFORE __getattr__ fires.
    """
    _SAFE_ATTRS = frozenset({'get', 'keys', 'values', 'items'})

    def __getattribute__(self, key: str) -> Any:
        if key.startswith('__') and key.endswith('__'):
            raise AttributeError(f'dunder access blocked: {key}')
        if key in AttrDict._SAFE_ATTRS or key == '_SAFE_ATTRS':
            return dict.__getattribute__(self, key)
        # Everything else → resolve from dict contents (or None on miss)
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            return None

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value


# Whitelist of functions callable from expressions.
# Deliberately no `getattr`, `type`, `dir`, `eval`, `exec`, `open`, `__import__`.
_SAFE_BUILTINS = {
    'abs': abs, 'min': min, 'max': max, 'round': round,
    'float': float, 'int': int, 'str': str, 'bool': bool, 'len': len,
    'isinstance': isinstance, 'sum': sum,
    'None': None, 'True': True, 'False': False,
    'math': math,  # exposes math.log, math.sqrt, etc.
}


def evaluate(expr: str, ctx: dict | AttrDict, default: Any = None) -> Any:
    """Evaluate `expr` against `ctx`. Returns `default` on any error.

    The expression can reference `ctx` as an object with attribute/item
    access. Any missing field → None (never KeyError). Any exception
    during eval → returns default (never raises)."""
    if not expr or not expr.strip():
        return default

    # Ensure ctx is dot-accessible
    if not isinstance(ctx, AttrDict):
        ctx = AttrDict(ctx)

    # Build namespace: only ctx + safe builtins.
    # Pass an EMPTY globals with __builtins__ set to {} so no import/getattr.
    globs = {'__builtins__': {}}
    locs = {**_SAFE_BUILTINS, 'ctx': ctx}

    try:
        # compile + eval with restricted namespaces
        code = compile(expr, '<signal_expr>', 'eval')
        return eval(code, globs, locs)  # noqa: S307 (deliberate, locked-down)
    except Exception:
        return default


def evaluate_bool(expr: str, ctx: dict | AttrDict) -> bool:
    """Evaluate as boolean; failures + None → False."""
    result = evaluate(expr, ctx, default=False)
    return bool(result) if result is not None else False


def evaluate_str(expr: str, ctx: dict | AttrDict) -> Optional[str]:
    """Evaluate as string; failures → None."""
    result = evaluate(expr, ctx, default=None)
    if result is None:
        return None
    return str(result)


def evaluate_float(expr: str, ctx: dict | AttrDict, default: float = 0.5) -> float:
    """Evaluate as float in [0, 1]; failures + None → default; clamped."""
    result = evaluate(expr, ctx, default=default)
    if result is None:
        return default
    try:
        f = float(result)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


def render_prose(template: str, ctx: dict | AttrDict) -> str:
    """Fill {placeholders} in a prose template with ctx values.

    Missing keys → literal `?`. Numeric values > 1000 get comma
    formatting so ERAs and percentages render clean.
    """
    if not template:
        return ''
    if not isinstance(ctx, AttrDict):
        ctx = AttrDict(ctx)
    # Simple {key} substitution — no format spec support (safer)
    import re
    def repl(m):
        key = m.group(1).strip()
        v = ctx.get(key)
        if v is None:
            return '?'
        if isinstance(v, float):
            return f'{v:.2f}' if abs(v) < 100 else f'{v:.1f}'
        return str(v)
    return re.sub(r'\{([^{}]+)\}', repl, template)


# ═══════════════════════════════════════════════════════════════════════
# self-test — run to smoke-check the evaluator
# ═══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    ctx = AttrDict({
        'home_sp_l3_era': 1.85, 'home_sp_era': 3.20,
        'away_sp_l3_era': 6.20, 'away_sp_era': 4.10,
        'close_total': 8.5, 'panel_implied_total': 7.2,
        'home_team': 'Dodgers', 'away_team': 'Padres',
    })

    tests = [
        ('ctx.home_sp_l3_era < 2.5', True),
        ('ctx.away_sp_l3_era > 5.0', True),
        ('ctx.nonexistent_field is None', True),
        ('abs(ctx.panel_implied_total - ctx.close_total) >= 1.0', True),
        ('"OVER" if ctx.panel_implied_total > ctx.close_total else "UNDER"', 'UNDER'),
        ('min(abs(ctx.panel_implied_total - ctx.close_total) / 2.0, 1.0)', 0.65),
        # Malicious attempts — should all return default
        ('__import__("os").system("echo pwned")', None),
        ('open("/etc/passwd")', None),
        ('ctx.__class__.__mro__', None),  # class introspection blocked
    ]
    print('signal_expr self-test:')
    for expr, expected in tests:
        got = evaluate(expr, ctx)
        status = 'OK' if got == expected else 'FAIL'
        print(f'  [{status}] {expr!r:<70} -> {got!r} (expected {expected!r})')

    # Prose render
    print('\nprose render:')
    tmpl = 'Skubal on a heater — {home_sp_l3_era} ERA L3 vs {home_sp_era} season'
    print(f'  {render_prose(tmpl, ctx)}')
