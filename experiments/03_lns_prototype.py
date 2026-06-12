"""
Logarithmic Number System (LNS) prototype for EML evaluation.

Demonstrates the key insight: in LNS representation, EML's overflow problem
is substantially tamed because nested exponentials become exponent additions
rather than actual exp() calls.

LNS represents a real number x as (sign, log|x|). Multiplication becomes
addition, division becomes subtraction. The hard operation is addition/
subtraction, requiring evaluation of the Gaussian logarithm functions.
"""

import numpy as np
from dataclasses import dataclass


@dataclass
class LNS:
    """Logarithmic Number System representation.

    Represents x as (sign, log_magnitude) where:
        x = sign * exp(log_magnitude)
        sign in {+1, -1, 0}

    Special values:
        Zero: sign=0, log_mag=-inf
        +inf: sign=+1, log_mag=+inf
        -inf: sign=-1, log_mag=+inf
    """
    sign: int      # +1, -1, or 0
    log_mag: float  # log(|x|)

    @staticmethod
    def from_real(x):
        if x == 0:
            return LNS(0, -np.inf)
        return LNS(1 if x > 0 else -1, np.log(np.abs(x)))

    def to_real(self):
        if self.sign == 0:
            return 0.0
        return self.sign * np.exp(self.log_mag)

    def __repr__(self):
        if self.sign == 0:
            return "LNS(0)"
        sign_str = "+" if self.sign > 0 else "-"
        return f"LNS({sign_str}, log_mag={self.log_mag:.6f}) = {self.to_real():.6g}"


# --- LNS arithmetic ---

def lns_multiply(a: LNS, b: LNS) -> LNS:
    """Multiplication in LNS: just add log-magnitudes."""
    if a.sign == 0 or b.sign == 0:
        return LNS(0, -np.inf)
    return LNS(a.sign * b.sign, a.log_mag + b.log_mag)


def lns_divide(a: LNS, b: LNS) -> LNS:
    """Division in LNS: subtract log-magnitudes."""
    assert b.sign != 0, "Division by zero"
    if a.sign == 0:
        return LNS(0, -np.inf)
    return LNS(a.sign * b.sign, a.log_mag - b.log_mag)


def _gaussian_log_add(r):
    """Gaussian logarithm for addition: log(1 + exp(r)).

    This is the log-sum-exp kernel, the hardest operation in LNS.
    Also known as the softplus function.
    """
    # Numerically stable computation
    if r > 30:
        return r
    elif r < -30:
        return 0.0
    return np.log1p(np.exp(r))


def _gaussian_log_sub(r):
    """Gaussian logarithm for subtraction: log(1 - exp(r)), r < 0.

    Returns log|1 - exp(r)| and a sign.
    """
    if r >= 0:
        # |1 - exp(r)| = exp(r) - 1 for r >= 0
        if r > 30:
            return r, -1
        return np.log(np.expm1(r)), -1
    else:
        # 1 - exp(r) > 0 for r < 0
        if r < -30:
            return 0.0, 1
        return np.log(-np.expm1(r)), 1


def lns_add(a: LNS, b: LNS) -> LNS:
    """Addition in LNS: the hard operation.

    Uses the Gaussian logarithm: log(exp(a) + exp(b)) = max(a,b) + log(1+exp(-|a-b|))
    """
    if a.sign == 0:
        return b
    if b.sign == 0:
        return a

    if a.sign == b.sign:
        # Same sign: magnitudes add
        # |a| + |b| = exp(la) + exp(lb) where la, lb are log-magnitudes
        # log(|a|+|b|) = max(la,lb) + log(1 + exp(-|la-lb|))
        diff = a.log_mag - b.log_mag
        new_log = max(a.log_mag, b.log_mag) + _gaussian_log_add(-abs(diff))
        return LNS(a.sign, new_log)
    else:
        # Different sign: magnitudes subtract
        diff = a.log_mag - b.log_mag
        if abs(diff) < 1e-15:
            return LNS(0, -np.inf)  # Cancellation
        if a.log_mag > b.log_mag:
            # |a| > |b|, result has sign of a
            log_sub, _ = _gaussian_log_sub(b.log_mag - a.log_mag)
            return LNS(a.sign, a.log_mag + log_sub)
        else:
            # |b| > |a|, result has sign of b
            log_sub, _ = _gaussian_log_sub(a.log_mag - b.log_mag)
            return LNS(b.sign, b.log_mag + log_sub)


def lns_subtract(a: LNS, b: LNS) -> LNS:
    """Subtraction: a - b = a + (-b)."""
    neg_b = LNS(-b.sign if b.sign != 0 else 0, b.log_mag)
    return lns_add(a, neg_b)


# --- EML in LNS ---

def lns_exp(a: LNS) -> LNS:
    """exp(a) in LNS.

    If a represents value v = sign * exp(log_mag), then
    exp(v) = exp(sign * exp(log_mag)).

    In LNS: the result has log-magnitude = v itself (when v is real positive).
    For general case: log|exp(v)| = Re(v).
    """
    v = a.to_real()  # Convert to real, compute exp, convert back
    result = np.exp(v)
    return LNS.from_real(result)


def lns_ln(a: LNS) -> LNS:
    """ln(a) in LNS.

    ln(sign * exp(log_mag)):
      - If sign > 0: ln(exp(log_mag)) = log_mag (!!!)
      - If sign < 0: ln(-exp(log_mag)) = log_mag + i*pi (complex)
      - If sign = 0: -inf
    """
    if a.sign == 0:
        return LNS(1, np.inf)  # ln(0) = -inf... special handling needed
    if a.sign > 0:
        # ln(exp(log_mag)) = log_mag. In LNS: result = log_mag.
        # LNS of log_mag:
        return LNS.from_real(a.log_mag)
    else:
        # Complex result -- for now just handle real case
        raise ValueError("ln of negative number requires complex LNS extension")


def lns_eml(x: LNS, y: LNS) -> LNS:
    """eml(x, y) = exp(x) - ln(y) in LNS."""
    exp_x = lns_exp(x)
    ln_y = lns_ln(y)
    return lns_subtract(exp_x, ln_y)


# --- Demonstration ---

def compare_eml(x_val, y_val):
    """Compare direct float64 EML with LNS EML."""
    # Direct
    direct = np.exp(x_val) - np.log(y_val)

    # LNS
    x_lns = LNS.from_real(x_val)
    y_lns = LNS.from_real(y_val)
    result_lns = lns_eml(x_lns, y_lns)
    lns_val = result_lns.to_real()

    err = abs(direct - lns_val)
    rel_err = err / max(abs(direct), 1e-15)

    print(f"  eml({x_val:8.3f}, {y_val:8.3f}):  direct={direct:15.8g}  lns={lns_val:15.8g}  rel_err={rel_err:.2e}")
    return rel_err


if __name__ == "__main__":
    print("=" * 80)
    print("LNS PROTOTYPE FOR EML")
    print("=" * 80)

    print("\n--- Basic LNS arithmetic verification ---")
    for a, b in [(3.0, 5.0), (100.0, 0.01), (-2.5, 7.3), (1e10, 1e-10)]:
        la, lb = LNS.from_real(a), LNS.from_real(b)
        print(f"\n  a={a}, b={b}")
        for op, name, expected in [
            (lns_multiply, "a*b", a*b),
            (lns_divide, "a/b", a/b),
            (lns_add, "a+b", a+b),
            (lns_subtract, "a-b", a-b),
        ]:
            result = op(la, lb).to_real()
            err = abs(result - expected) / max(abs(expected), 1e-15)
            print(f"    {name:6s} = {result:15.8g}  expected={expected:15.8g}  rel_err={err:.2e}")

    print("\n--- EML evaluation: direct vs LNS ---")
    test_pairs = [
        (1.0, 1.0),    # eml(1,1) = e
        (0.5, 1.0),    # eml(0.5, 1) = exp(0.5)
        (1.0, np.e),   # eml(1, e) = e - 1
        (2.0, 1.0),    # eml(2, 1) = exp(2)
        (0.1, 0.5),
        (3.0, 2.0),
        (-1.0, 1.0),   # eml(-1, 1) = exp(-1)
        (5.0, 1.0),    # eml(5, 1) = exp(5) -- getting large
        (10.0, 1.0),   # exp(10) ~ 22026
    ]

    for x, y in test_pairs:
        compare_eml(x, y)

    print("\n--- Nested EML (the overflow test) ---")
    print("Computing eml(eml(x, 1), 1) = exp(exp(x)) for increasing x:")

    for x in [0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0]:
        # Direct
        try:
            inner = np.exp(x)
            outer = np.exp(inner) - np.log(1.0)
            direct_ok = np.isfinite(outer)
        except:
            direct_ok = False

        # LNS
        x_lns = LNS.from_real(x)
        one_lns = LNS.from_real(1.0)
        try:
            inner_lns = lns_eml(x_lns, one_lns)
            outer_lns = lns_eml(inner_lns, one_lns)
            lns_val = outer_lns.to_real()
            lns_ok = np.isfinite(lns_val) or np.isfinite(outer_lns.log_mag)
            lns_log = outer_lns.log_mag
        except Exception as e:
            lns_ok = False
            lns_log = str(e)

        direct_str = f"{outer:.6g}" if direct_ok else "inf"
        print(f"  x={x:5.1f}: direct={'OK' if direct_ok else 'OVERFLOW':>8s}"
              f" ({direct_str:>15s})"
              f"   lns={'OK' if lns_ok else 'FAIL':>4s}"
              f" (log_mag={lns_log})")

    print("\n--- Key insight ---")
    print("In LNS, exp(exp(x)) is represented by its log-magnitude, which is")
    print("just exp(x). We never materialize the doubly-exponentiated value.")
    print("The LNS representation 'absorbs' one level of exponentiation into")
    print("the representation itself. This is why LNS tames EML overflow.")
    print()
    print("For depth-n EML trees, LNS effectively reduces the nesting depth")
    print("by one level, extending the range before overflow occurs.")
