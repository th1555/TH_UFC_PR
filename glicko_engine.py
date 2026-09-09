"""
glicko_engine.py

The Glicko-2 rating engine for the UFC live-ratings build-out.

PROVENANCE AND FAITHFULNESS CONTRACT
------------------------------------
Every numerical expression in this module is transcribed unchanged from
notebook 11 (blocks 2 to 9 of 11_glicko2_implementation.ipynb). Only the
comments, the structure, and the light type hints are new. This is
deliberate: the first acceptance gate for the live system is that the
extracted engine reproduces the frozen ratings exactly when fed the same
score sequence. If the arithmetic here drifts from the notebook, that gate
is meaningless, so treat the maths as read-only and put any new behaviour
(ledger I/O, score computation, the replay loop) in the runner, not here.

WHAT THIS MODULE IS
-------------------
Pure functions only: scale conversion, the two Glicko helper functions,
the volatility solver, the full per-period update, and the inactivity
decay. It holds no state, reads no files, and knows nothing about UFC
data. The replay loop that walks the bout ledger and the code that turns
raw fight facts into a score both live in the runner (next artefact).

RATING PERIOD MODEL (important, and non-standard)
-------------------------------------------------
One rating period is one fight. Inactivity decay is NOT applied as a
periodic batch step over idle fighters; it is applied lazily, only to the
two fighters about to compete, based on each one's own elapsed days since
their last fight (see apply_inactivity_decay). A fighter who never returns
is simply never decayed until they do. This is why the live update can run
the identical code path as the historical seed: there is no periodic clock
to keep in sync, only per-fighter elapsed days.

SELF-TEST
---------
Running this file directly (python glicko_engine.py) runs the Glickman
(2013) worked example, which needs no UFC data. A pass confirms the
extraction is faithful before any of your seed data is involved.
Expected: rating 1464.06, RD 151.52, volatility ~0.05999.
"""

import math

# ---------------------------------------------------------------------------
# System constants (Glickman 2013, Section 2)
# ---------------------------------------------------------------------------
# Glicko-2 works on an internal scale centred at 0; display ratings are on
# the traditional scale centred at 1500. SCALE converts between the two.

SCALE = 173.7178              # internal-to-display conversion factor
MU = 0.0                      # default rating on the internal scale (display 1500)
PHI = 350 / SCALE             # default RD on the internal scale (display 350), approx 2.0147
SIGMA = 0.06                  # default volatility (Glickman's suggested starting point)
TAU = 0.5                     # constrains how fast volatility can move per period;
                              # smaller is more conservative
EPSILON = 0.000001            # convergence tolerance for the volatility solver

# MMA-specific: layoffs erode form faster than the chess default implies, so
# the volatility used for inactivity decay is floored. 0.25 over-penalised
# routine gaps between fights; 0.18 was the calibrated compromise.
SIGMA_INACTIVE_FLOOR = 0.18

# One inactivity "period" for decay purposes. Calibrated from the median
# inter-fight gap in the data (168 days), rounded to 180. A fighter out for
# 360 days has accrued two periods of RD growth.
RATING_PERIOD_DAYS = 180


# ---------------------------------------------------------------------------
# Scale conversion (Glickman 2013, Steps 1 and 8)
# ---------------------------------------------------------------------------

def scale_down(rating: float, rd: float) -> tuple[float, float]:
    """Display scale (centred 1500) to internal Glicko-2 scale (centred 0)."""
    mu = (rating - 1500) / SCALE
    phi = rd / SCALE
    return mu, phi


def scale_up(mu: float, phi: float) -> tuple[float, float]:
    """Internal Glicko-2 scale back to the display scale."""
    rating = mu * SCALE + 1500
    rd = phi * SCALE
    return rating, rd


# ---------------------------------------------------------------------------
# Glicko helper functions g(phi) and E (Glickman 2013, Step 3)
# ---------------------------------------------------------------------------

def g(phi: float) -> float:
    """
    Weight an opponent's contribution by their rating certainty.

    A high-RD (uncertain) opponent should move your rating less than a
    well-established one, win or lose. g shrinks towards 0 as phi grows.
    """
    return 1 / math.sqrt(1 + 3 * phi**2 / math.pi**2)


def expected_score(mu: float, mu_j: float, phi_j: float) -> float:
    """
    Expected result for a fighter (mu) against opponent j (mu_j, phi_j).

    A logistic in the rating gap, with the gap damped by the opponent's
    uncertainty via g(phi_j). Equal ratings give 0.5.
    """
    return 1 / (1 + math.exp(-g(phi_j) * (mu - mu_j)))


# ---------------------------------------------------------------------------
# Volatility update (Glickman 2013, Step 5)
# ---------------------------------------------------------------------------

def update_volatility(phi: float, sigma: float, v: float, delta: float) -> float:
    """
    Solve for the new volatility sigma' using Glickman's Illinois algorithm
    (a bracketed root-finder that converges more reliably than plain
    regula falsi).

    Inputs:
        phi:   current RD on the internal scale
        sigma: current volatility
        v:     estimated variance from this period's outcomes (Step 3)
        delta: estimated rating improvement from the outcomes (Step 4)

    Returns:
        sigma_new: the updated volatility
    """
    # f(x) is the function whose root gives log(sigma'^2).
    a = math.log(sigma**2)

    def f(x: float) -> float:
        ex = math.exp(x)
        num1 = ex * (delta**2 - phi**2 - v - ex)
        den1 = 2 * (phi**2 + v + ex)**2
        return num1 / den1 - (x - a) / TAU**2

    # Initial bracket [A, B]. A is always log(sigma^2).
    A = a

    # Choose B so that the root is bracketed. If delta^2 is large enough the
    # closed form applies; otherwise step B down by TAU until f(B) turns
    # negative.
    if delta**2 > phi**2 + v:
        B = math.log(delta**2 - phi**2 - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU

    # Illinois iteration.
    fA = f(A)
    fB = f(B)

    while abs(B - A) > EPSILON:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)

        if fC * fB <= 0:
            # Root lies between C and B; move A up to the old B.
            A = B
            fA = fB
        else:
            # Illinois adjustment: halve fA to avoid a stalled endpoint.
            fA = fA / 2

        B = C
        fB = fC

    sigma_new = math.exp(A / 2)
    return sigma_new


# ---------------------------------------------------------------------------
# Full per-period rating update (Glickman 2013, Steps 2 to 8)
# ---------------------------------------------------------------------------

def update_rating(mu: float, phi: float, sigma: float,
                  opponents: list[tuple[float, float]],
                  outcomes: list[float]) -> tuple[float, float, float]:
    """
    Update one fighter's state after one rating period (one fight in this
    system, so opponents and outcomes are normally length 1).

    Inputs:
        mu, phi, sigma: the fighter's current state on the internal scale
        opponents:      list of (mu_j, phi_j) tuples, one per opponent faced
        outcomes:       list of scores, one per opponent
                        (1.0 win, 0.0 loss, 0.5 draw, or a continuous-S value)

    Returns:
        (mu_new, phi_new, sigma_new) on the internal scale.

    Note: outcomes here are already the score to apply. The continuous-S
    dominance value is computed upstream in the runner and passed in as the
    outcome; the engine does not know or care how the score was derived.
    """
    # No opponents means the fighter sat this period out. In this build-out
    # that path is not driven directly (inactivity is handled lazily by
    # apply_inactivity_decay in the runner), but it is kept faithful to the
    # source: RD grows by one volatility step and nothing else moves.
    if not opponents:
        phi_new = math.sqrt(phi**2 + sigma**2)
        return mu, phi_new, sigma

    # Step 3 and 4: accumulate the variance and improvement contributions
    # over every opponent in the period.
    v_sum = 0.0
    delta_sum = 0.0

    for (mu_j, phi_j), outcome in zip(opponents, outcomes):
        g_j = g(phi_j)
        E_j = expected_score(mu, mu_j, phi_j)

        # Variance contribution from this matchup.
        v_sum += g_j**2 * E_j * (1 - E_j)

        # Improvement contribution: actual minus expected, weighted by g.
        delta_sum += g_j * (outcome - E_j)

    v = 1.0 / v_sum
    delta = v * delta_sum

    # Step 5: new volatility.
    sigma_new = update_volatility(phi, sigma, v, delta)

    # Step 6: RD first grows by the new volatility (pre-update inflation)...
    phi_star = math.sqrt(phi**2 + sigma_new**2)
    # ...then shrinks by the information gained this period.
    phi_new = 1.0 / math.sqrt(1.0 / phi_star**2 + 1.0 / v)

    # Step 7: new rating. Uses delta_sum directly (mu' = mu + phi'^2 * sum).
    mu_new = mu + phi_new**2 * delta_sum

    return mu_new, phi_new, sigma_new


# ---------------------------------------------------------------------------
# Inactivity decay
# ---------------------------------------------------------------------------

def apply_inactivity_decay(phi: float, sigma: float, elapsed_days: float) -> float:
    """
    Grow a fighter's RD to reflect uncertainty accrued while inactive.

    Applied lazily as a pre-step to a fighter about to compete, based on
    the days since their own last fight. Growth scales with the number of
    180-day periods elapsed and is driven by the (floored) volatility.

    Only RD (phi) changes here; the rating (mu) is untouched. Inactivity in
    this model widens the confidence band, it does not drift the rating
    towards the mean. That is a deliberate modelling choice: "current
    rating" for a long-idle fighter means their level when last seen, now
    held with low confidence.

    Inputs:
        phi:          current RD on the internal scale
        sigma:        current volatility
        elapsed_days: days since the fighter's last fight

    Returns:
        phi_decayed: the grown RD, capped at the default RD (display 350).
    """
    # First fight, same-day, or a bad/backwards date: no decay.
    if elapsed_days is None or elapsed_days <= 0:
        return phi

    n_periods = elapsed_days / RATING_PERIOD_DAYS

    # Floor the volatility used for decay so that fighters with an
    # artificially low sigma still accrue realistic layoff uncertainty.
    sigma_inactive = max(sigma, SIGMA_INACTIVE_FLOOR)
    phi_decayed = math.sqrt(phi**2 + n_periods * sigma_inactive**2)

    # Cap at the default RD so a multi-year layoff saturates rather than
    # producing an absurd uncertainty.
    phi_decayed = min(phi_decayed, PHI)
    return phi_decayed


# ---------------------------------------------------------------------------
# Self-test: Glickman (2013) worked example (no UFC data required)
# ---------------------------------------------------------------------------

def run_glickman_example(verbose: bool = True) -> bool:
    """
    Reproduce the worked example from Glickman's Glicko-2 paper. A player
    rated 1500 (RD 200, sigma 0.06) faces three opponents in one period and
    should end at rating 1464.06, RD 151.52, volatility ~0.05999.

    Returns True if all three land within tolerance.
    """
    player_rating, player_rd, player_sigma = 1500, 200, 0.06
    mu, phi = scale_down(player_rating, player_rd)

    # (opponent rating, opponent RD, outcome for our player)
    opp_data = [
        (1400, 30, 1.0),   # win
        (1550, 100, 0.0),  # loss
        (1700, 300, 0.0),  # loss
    ]

    opponents, outcomes = [], []
    for rating_j, rd_j, outcome in opp_data:
        mu_j, phi_j = scale_down(rating_j, rd_j)
        opponents.append((mu_j, phi_j))
        outcomes.append(outcome)

    mu_new, phi_new, sigma_new = update_rating(
        mu, phi, player_sigma, opponents, outcomes
    )
    rating_new, rd_new = scale_up(mu_new, phi_new)

    rating_ok = abs(rating_new - 1464.06) < 1.0
    rd_ok = abs(rd_new - 151.52) < 1.0
    sigma_ok = abs(sigma_new - 0.05999) < 0.001
    passed = rating_ok and rd_ok and sigma_ok

    if verbose:
        print("GLICKO-2 ENGINE SELF-TEST (Glickman 2013 worked example)")
        print(f"  New rating:     {rating_new:.2f}   (expected 1464.06)  {'ok' if rating_ok else 'FAIL'}")
        print(f"  New RD:         {rd_new:.2f}   (expected 151.52)  {'ok' if rd_ok else 'FAIL'}")
        print(f"  New volatility: {sigma_new:.5f}  (expected ~0.05999)  {'ok' if sigma_ok else 'FAIL'}")
        print()
        print("PASSED" if passed else "FAILED")

    return passed


if __name__ == "__main__":
    ok = run_glickman_example()
    # Non-zero exit on failure so this can be wired into a CI check later.
    raise SystemExit(0 if ok else 1)
