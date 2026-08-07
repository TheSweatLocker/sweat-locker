"""Pipeline-vs-model consistency check (2026-08-07).

Detects when the pipeline's final pick direction contradicts the raw
model projection direction. Surfaced during 8/7 slate audit — 5 games
had picks going opposite to their own Jerry-model numbers:
  BAL @ TEX: UNDER 7.5 picked, jerry_pred_total = 8.95 (over)
  LAD @ ARI: UNDER 9.0 picked, jerry_pred_total = 11.44 (over)
  COL @ STL: OVER 8.5 picked, jerry_pred_total = 7.83 (under)
  HOU @ SD:  UNDER 8.5 picked, jerry_pred_total = 10.30 (over)
  CIN @ WSN: Nats ML picked, all models see 9-10 runs → OVER is play

Calibration overrides can legitimately flip direction (family suppress,
fair-price fade, cohort cap, form-drift trap). But when 5+ picks/night
contradict raw math without a stated calibration reason, that's a
signal something's off.

This module provides a pure diagnostic — flags the contradiction, does
NOT auto-adjust picks. Downstream consumers (confluence audit, Jerry
prompt, sweat card) can weight the flag as they see fit.

Sport-universal — works on any game_context row with:
  - close_total / close_spread (market line)
  - jerry_pred_total / jerry_pred_spread (model projection)
  - a pick market + side + line

Interface:
    from pipeline_consistency import check_pick_vs_model
    result = check_pick_vs_model(game_ctx, 'total', 'UNDER', 7.5)
    # → {
    #   'consistent': bool,
    #   'model_direction': 'over' | 'under' | 'flat' | None,
    #   'pick_direction': 'over' | 'under' | 'flat' | None,
    #   'gap': float,     # model projection minus line (signed)
    #   'note': str,      # human-readable summary
    # }
"""
from __future__ import annotations
from typing import Optional


# Ignore small model-vs-line gaps as noise (bettors don't get paid on
# fractional edges anyway). 0.5 for totals (half-run), 0.5 for spreads.
_TOTAL_NOISE_THRESHOLD = 0.5
_SPREAD_NOISE_THRESHOLD = 0.5


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def check_pick_vs_model(ctx: dict, market: str, pick_side: Optional[str],
                        pick_line: Optional[float] = None) -> dict:
    """Compare pipeline pick direction to Jerry model direction.

    ctx: game_context row (dict) — must have jerry_pred_total /
         jerry_pred_spread and close_total / close_spread when relevant
    market: 'total' | 'ml' | 'spread' | 'rl'
    pick_side: 'OVER' | 'UNDER' | 'HOME' | 'AWAY' | None
    pick_line: numeric line if applicable (else derives from ctx)

    Returns dict with consistent flag, directions, gap, note.
    Returns {'consistent': None, 'note': 'n/a'} when data missing.
    """
    market = (market or '').lower()
    pick_side = (pick_side or '').upper() if pick_side else None
    result = {'consistent': None, 'model_direction': None,
              'pick_direction': None, 'gap': None, 'note': 'n/a'}

    if market == 'total':
        model = _f(ctx.get('jerry_pred_total'))
        line = _f(pick_line) if pick_line is not None else _f(ctx.get('close_total'))
        if model is None or line is None: return result
        gap = model - line
        model_dir = ('over' if gap > _TOTAL_NOISE_THRESHOLD
                     else 'under' if gap < -_TOTAL_NOISE_THRESHOLD
                     else 'flat')
        pick_dir = ('over' if pick_side == 'OVER'
                    else 'under' if pick_side == 'UNDER' else None)
        result['model_direction'] = model_dir
        result['pick_direction'] = pick_dir
        result['gap'] = round(gap, 2)
        if pick_dir is None:
            result['consistent'] = None
            result['note'] = 'pick side unknown'
        elif model_dir == 'flat':
            result['consistent'] = True  # flat model agrees with anything
            result['note'] = f'model {model:.2f} flat vs line {line} (gap {gap:+.2f})'
        elif model_dir == pick_dir:
            result['consistent'] = True
            result['note'] = f'model {model:.2f} agrees with {pick_dir.upper()} (gap {gap:+.2f})'
        else:
            result['consistent'] = False
            result['note'] = (f'⚠ INVERSION: pipeline picked {pick_dir.upper()} '
                              f'but jerry_pred_total {model:.2f} vs line {line} '
                              f'implies {model_dir.upper()} (gap {gap:+.2f})')
        return result

    if market in ('ml', 'spread', 'rl'):
        # For ML/spread, model direction is derived from jerry_pred_spread.
        # jerry_pred_spread = home advantage margin (+ = home favored)
        # market close_spread convention: - = home favored
        # spread_delta = model_spread + close_spread (positive = home lean)
        model = _f(ctx.get('jerry_pred_spread'))
        close_spread = _f(ctx.get('close_spread'))
        if model is None or close_spread is None: return result
        # + jerry_pred_spread = home more favored than market shows
        # Add close_spread to convert: if model says home wins by 2 (+2)
        # and market says home fav by 1.5 (close_spread = -1.5), then
        # delta = 2 + (-1.5) = +0.5 → home lean
        delta = model + close_spread
        model_dir = ('home' if delta > _SPREAD_NOISE_THRESHOLD
                     else 'away' if delta < -_SPREAD_NOISE_THRESHOLD
                     else 'flat')
        pick_dir = ('home' if pick_side == 'HOME'
                    else 'away' if pick_side == 'AWAY' else None)
        result['model_direction'] = model_dir
        result['pick_direction'] = pick_dir
        result['gap'] = round(delta, 2)
        if pick_dir is None:
            result['consistent'] = None
            result['note'] = 'pick side unknown'
        elif model_dir == 'flat':
            result['consistent'] = True
            result['note'] = f'model spread {model:+.2f} vs line {close_spread:+.2f} → coinflip (delta {delta:+.2f})'
        elif model_dir == pick_dir:
            result['consistent'] = True
            result['note'] = f'model spread agrees with {pick_dir.upper()} (delta {delta:+.2f})'
        else:
            result['consistent'] = False
            result['note'] = (f'⚠ INVERSION: pipeline picked {pick_dir.upper()} '
                              f'but jerry_pred_spread {model:+.2f} + close_spread '
                              f'{close_spread:+.2f} = delta {delta:+.2f} implies {model_dir.upper()}')
        return result

    return result


if __name__ == '__main__':
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try: sys.stdout.reconfigure(encoding='utf-8')
        except Exception: pass
    # Self-tests using tonight's actual inversion cases
    cases = [
        ('BAL@TEX UNDER 7.5',
         {'jerry_pred_total': 8.95, 'close_total': 7.5},
         'total', 'UNDER', 7.5, False),
        ('LAD@ARI UNDER 9.0',
         {'jerry_pred_total': 11.44, 'close_total': 9.0},
         'total', 'UNDER', 9.0, False),
        ('ATL@NYY OVER 8.0 (should be consistent)',
         {'jerry_pred_total': 9.09, 'close_total': 8.0},
         'total', 'OVER', 8.0, True),
        ('MIN@MIL Brewers ML (should be consistent)',
         {'jerry_pred_spread': 2.62, 'close_spread': -1.5},
         'ml', 'HOME', None, True),
        ('Flat model / any pick',
         {'jerry_pred_total': 8.4, 'close_total': 8.5},
         'total', 'OVER', 8.5, True),
    ]
    print(f'{"CASE":45s} {"CONSISTENT":11s} EXPECTED  NOTE')
    print('-' * 100)
    for name, ctx, mkt, side, line, expected in cases:
        r = check_pick_vs_model(ctx, mkt, side, line)
        got = r['consistent']
        match = '✓' if got == expected else '✗'
        print(f'{name:45s} {str(got):11s} {str(expected):9s} {match}  {r["note"][:60]}')
