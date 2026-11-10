"""Exact probability calculations for Part 2; Python standard library only.

No simulation, service, Docker, or third-party packages are required.
"""
from decimal import Decimal, localcontext
import argparse
import json

D = Decimal
PRECISION = 80


def validate(n, probability):
    if type(n) is not int or n < 1:
        raise ValueError("batch size must be a positive integer")
    if not D(0) < probability < D(1):
        raise ValueError("probability must be strictly between zero and one")


def uniform_cdf(t):
    """CDF of an original or duplicate's own duration, in milliseconds."""
    return max(D(0), min(D(1), (t - 50) / 350))


def completion_cdf(t, duplicates=False):
    """One logical request's completion time, measured from original arrival.

    A hypothetical duplicate duration can be sampled even when no duplicate is
    launched: if X <= 200, then X < 200 + Y and the minimum still equals X.
    """
    original = uniform_cdf(t)
    if not duplicates:
        return original
    duplicate = uniform_cdf(t - 200)
    if duplicate == 0:
        return original
    return 1 - (1 - original) * (1 - duplicate)


def batch_cdf(latency_ms, n, duplicates=False):
    return completion_cdf(latency_ms - 1000, duplicates) ** n


def quantile(n, probability=D("0.95"), duplicates=False):
    """Closed-form batch latency quantile with stable upper-tail arithmetic."""
    validate(n, probability)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        q = (probability.ln() / n).exp()
        delta = 1 - q
        if not duplicates or q <= D(4) / 7:
            return +(D(1050) + 350 * q)
        # If s = 400 - t, s(s + 200) = 350**2 * delta.
        # Rationalization avoids subtracting two nearby square roots.
        s = 122500 * delta / ((10000 + 122500 * delta).sqrt() + 100)
        return +(1400 - s)


def inverted_quantile(n, probability=D("0.95"), duplicates=False):
    """Independent bisection of the batch CDF, for verification."""
    validate(n, probability)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        low, high = D(1050), D(1400)
        for _ in range(240):
            middle = (low + high) / 2
            if batch_cdf(middle, n, duplicates) < probability:
                low = middle
            else:
                high = middle
        return +((low + high) / 2)


def results():
    with localcontext() as ctx:
        ctx.prec = PRECISION
        rows = []
        for n in (100, 10000):
            baseline = quantile(n)
            hedged = quantile(n, duplicates=True)
            rows.append({
                "users": n,
                "baseline_batch_p95_ms": f"{baseline:.9f}",
                "duplicates_batch_p95_ms": f"{hedged:.9f}",
                "improvement_ms": f"{baseline - hedged:.9f}",
                "relative_batch_improvement_percent": f"{100 * (baseline - hedged) / baseline:.9f}",
            })
        return {
            "method": "analytic formulas, 80 significant decimal digits; presentation rounded to 9 decimals",
            "latencies": rows,
            "duplicate_percent_of_all_original_requests": f"{100 * D(4) / 7:.9f}",
            "duplicate_wins_percent_of_all_original_requests": f"{100 * D(9) / 98:.9f}",
            "duplicate_wins_percent_of_duplicated_requests": f"{100 * D(9) / 56:.9f}",
        }


def verify():
    with localcontext() as ctx:
        ctx.prec = PRECISION
        for n in (100, 10000):
            for duplicates in (False, True):
                answer = quantile(n, duplicates=duplicates)
                numerical = inverted_quantile(n, duplicates=duplicates)
                if abs(answer - numerical) > D("1e-60"):
                    raise AssertionError("analytic and numerical quantiles disagree")
                if abs(batch_cdf(answer, n, duplicates) - D("0.95")) > D("1e-60"):
                    raise AssertionError("calculated quantile does not yield batch CDF 0.95")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="also invert the CDF independently")
    args = parser.parse_args()
    if args.verify:
        verify()
    print(json.dumps(results(), indent=2))
    if args.verify:
        print("Verified: closed forms and CDF bisection agree within 1e-60 ms.")
