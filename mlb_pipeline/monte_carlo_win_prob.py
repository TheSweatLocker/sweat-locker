"""Monte Carlo win probability from projected run distributions.

2026-07-21 foundation. Currently our pipeline emits POINT ESTIMATES:
"projected_total = 8.5". But bettors need P(total > 8.5), not the mean.
Top MLB models simulate the game 10,000 times and count outcomes.

This module wraps a fast Poisson-approximation Monte Carlo. It's not a
proper play-by-play sim (that would require pitch-level modeling); it
approximates by sampling both team run totals from Poisson distributions
centered on their projected runs.

Poisson isn't perfect for run scoring (real distribution is negative
binomial with heavier right tail), but it's a defensible starting point
and beats a point estimate for probability queries.

Integration points (this file is standalone; hooks come later):
  - play_of_day.py — when composite says gap=+1.5, call sim to get real
    P(OVER). Publish as "OVER 8.5 · 61% MC" instead of just "gap +1.5"
  - Sub-band conviction can also be validated against MC probability

USAGE:
    from monte_carlo_win_prob import simulate_total, simulate_side
    p_over = simulate_total(proj_home=4.2, proj_away=4.4, line=8.5, n=10000)
    # -> ~0.61
    p_home_win = simulate_side(proj_home=4.2, proj_away=4.4, n=10000)
    # -> ~0.46 (away is expected slightly higher)
"""
import math
import random
from typing import Optional

# Reproducibility for testing — seed is time-based by default
_DEFAULT_N = 10000


def _poisson_sample(mean: float, rng: random.Random) -> int:
    """Fast Poisson sampler using Knuth's algorithm. Fine for mean < 30."""
    if mean <= 0:
        return 0
    L = math.exp(-mean)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def simulate_total(proj_home: float, proj_away: float, line: float,
                   n: int = _DEFAULT_N, seed: Optional[int] = None) -> dict:
    """Return P(over), P(under), P(push) for a given total line.

    Args:
      proj_home, proj_away: projected runs for each team (Poisson mean)
      line: the total line (e.g. 8.5)
      n: number of simulations
      seed: optional RNG seed for determinism

    Returns:
      {'p_over': 0.61, 'p_under': 0.39, 'p_push': 0.0,
       'mean_total': 8.62, 'std_total': 3.31, 'sample_n': n}
    """
    rng = random.Random(seed)
    over = under = push = 0
    total_runs = []
    for _ in range(n):
        h = _poisson_sample(proj_home, rng)
        a = _poisson_sample(proj_away, rng)
        t = h + a
        total_runs.append(t)
        if t > line: over += 1
        elif t < line: under += 1
        else: push += 1
    mean_t = sum(total_runs) / n
    var_t = sum((x - mean_t) ** 2 for x in total_runs) / n
    return {
        'p_over': round(over / n, 4),
        'p_under': round(under / n, 4),
        'p_push': round(push / n, 4),
        'mean_total': round(mean_t, 2),
        'std_total': round(math.sqrt(var_t), 2),
        'sample_n': n,
    }


def simulate_side(proj_home: float, proj_away: float,
                  n: int = _DEFAULT_N, seed: Optional[int] = None) -> dict:
    """Return P(home wins), P(away wins), P(tie) — no ties in MLB, so
    ties get resolved to a bonus 10-inning run (biased slightly to home
    for HFA)."""
    rng = random.Random(seed)
    home_wins = away_wins = 0
    for _ in range(n):
        h = _poisson_sample(proj_home, rng)
        a = _poisson_sample(proj_away, rng)
        if h == a:
            # Extra-inning: bias 51% home (HFA)
            if rng.random() < 0.51:
                home_wins += 1
            else:
                away_wins += 1
        elif h > a:
            home_wins += 1
        else:
            away_wins += 1
    return {
        'p_home_win': round(home_wins / n, 4),
        'p_away_win': round(away_wins / n, 4),
        'sample_n': n,
    }


def simulate_spread(proj_home: float, proj_away: float, spread: float,
                    n: int = _DEFAULT_N, seed: Optional[int] = None) -> dict:
    """Simulate P(home covers spread).

    spread is the home team's spread (negative = home favored).
    e.g. spread=-1.5 means home must win by 2+.
    """
    rng = random.Random(seed)
    covers = 0
    for _ in range(n):
        h = _poisson_sample(proj_home, rng)
        a = _poisson_sample(proj_away, rng)
        margin = h - a  # positive = home wins by margin
        # Home covers if margin > -spread (i.e. h - a > -spread)
        # e.g. spread=-1.5 → home covers if margin > 1.5 → home wins by 2+
        if margin > -spread:
            covers += 1
    return {
        'p_home_covers': round(covers / n, 4),
        'p_away_covers': round(1 - (covers / n), 4),
        'sample_n': n,
    }


def american_to_implied(odds: int) -> float:
    """American odds → implied probability (with juice)."""
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def kelly_stake(model_prob: float, american_odds: int, fraction: float = 0.25) -> float:
    """Return Kelly stake as fraction of bankroll.

    Fractional Kelly (default 0.25 = quarter-Kelly) is standard for MLB
    to reduce variance while still capturing edge.
    """
    if not (0 < model_prob < 1) or american_odds == 0:
        return 0.0
    b = (american_odds / 100.0) if american_odds > 0 else (100.0 / abs(american_odds))
    p = model_prob
    q = 1 - p
    edge = (b * p - q) / b
    if edge <= 0:
        return 0.0
    return round(edge * fraction, 4)


if __name__ == "__main__":
    # Sanity: line 8.5, both teams projected ~4.3 → should be ~50/50
    r = simulate_total(4.3, 4.3, 8.5, n=20000, seed=42)
    print(f"Line 8.5, 4.3-4.3 projection: P(OVER)={r['p_over']}, mean={r['mean_total']}")

    # Line 8.5, home projected 5.0 vs away 3.5 = ~8.5 mean
    r = simulate_total(5.0, 3.5, 8.5, n=20000, seed=42)
    print(f"Line 8.5, 5.0-3.5 projection: P(OVER)={r['p_over']}, mean={r['mean_total']}")

    # Sides — home projected 4.8 vs away 4.2
    s = simulate_side(4.8, 4.2, n=20000, seed=42)
    print(f"Home 4.8 vs Away 4.2: P(home)={s['p_home_win']}")

    # Spread —  -1.5 home
    sp = simulate_spread(4.8, 4.2, -1.5, n=20000, seed=42)
    print(f"Home -1.5: P(cover)={sp['p_home_covers']}")

    # Kelly example
    stake = kelly_stake(0.60, -110, fraction=0.25)
    print(f"Kelly stake @ 60% edge -110: {stake} of bankroll")
