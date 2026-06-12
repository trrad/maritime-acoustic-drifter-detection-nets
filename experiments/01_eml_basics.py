"""
EML basics: verify the core identities from the paper in numpy.

Implements eml(x,y) = exp(x) - ln(y) over complex128 and reconstructs
elementary functions from it. Reproduces key entries from Table 4.
"""

import numpy as np

# --- Core operator ---

def eml(x, y):
    """The EML operator: exp(x) - ln(y), over complex domain."""
    return np.exp(x) + 0j - np.log(y + 0j)


# --- Constants ---

def const_e():
    """e = eml(1, 1) = exp(1) - ln(1) = e - 0 = e"""
    return eml(1, 1)

def const_zero():
    """0 = ln(1) = eml(1, eml(eml(1, 1), 1)). K=7.
    RPN: 1 1 1 E 1 E E — seven tokens.
    """
    return eml(1, eml(eml(1, 1), 1))  # ln(1) = 0


# --- Functions from paper ---

def eml_exp(x):
    """exp(x) = eml(x, 1). K=3. Trivial."""
    return eml(x, 1)

def eml_ln(z):
    """ln(z) = eml(1, eml(eml(1, z), 1)). K=7. [Eq. 5]
    Derivation: eml(1, w) = e - ln(w), so if w = eml(eml(1,z), 1) = exp(eml(1,z)) - 0
    = exp(e - ln(z)) = e^e / z ... hmm. Let's just verify numerically.
    """
    return eml(1, eml(eml(1, z), 1))


# --- Verification ---

def verify(name, eml_result, expected, tol=1e-10):
    """Compare EML-computed value to expected, report."""
    eml_val = np.real(eml_result) if np.abs(np.imag(eml_result)) < tol else eml_result
    err = np.abs(eml_val - expected)
    status = "OK" if err < tol else "FAIL"
    print(f"  {status:4s}  {name:20s}  eml={eml_val:>20.12g}  expected={expected:>20.12g}  err={err:.2e}")
    return err < tol


if __name__ == "__main__":
    print("=" * 90)
    print("EML BASIC IDENTITIES")
    print("=" * 90)

    # Test points (use transcendentals to avoid coincidental equalities)
    x_test = np.array([0.1, 0.5, 1.0, np.e, np.pi, -0.5, -1.0, 2.7])
    y_test = np.array([0.3, 0.7, 1.0, 2.0, np.pi, 0.1, 3.5, 1.5])

    print("\n--- Constants ---")
    verify("e", const_e(), np.e)
    verify("0", const_zero(), 0.0)

    print("\n--- exp(x) = eml(x, 1) ---")
    for x in x_test:
        verify(f"exp({x:.2f})", eml_exp(x), np.exp(x))

    print("\n--- ln(z) = eml(1, eml(eml(1, z), 1)) ---")
    for z in x_test[x_test > 0]:  # ln only for positive reals first
        verify(f"ln({z:.2f})", eml_ln(z), np.log(z))

    # Now test with negative reals (needs complex branch)
    print("\n--- ln(z) for negative z (complex branch) ---")
    for z in [-0.5, -1.0]:
        result = eml_ln(z)
        expected = np.log(complex(z))
        err = np.abs(result - expected)
        # Note: may differ by 2*pi*i due to branch cut -- paper mentions this
        err_mod = min(err, np.abs(result - expected + 2j*np.pi), np.abs(result - expected - 2j*np.pi))
        status = "OK" if err_mod < 1e-10 else "FAIL"
        print(f"  {status:4s}  ln({z:.2f}):  eml={str(result):>30s}  expected={str(expected):>30s}  err={err:.2e}  err_mod2pi={err_mod:.2e}")

    # --- Build up more functions via bootstrapping ---
    print("\n" + "=" * 90)
    print("BOOTSTRAPPED FUNCTIONS")
    print("=" * 90)

    # Once we have exp and ln, we can build everything else.
    # Let's verify the bootstrapping chain from Fig. 1.

    # Subtraction: x - y.  K=11 from direct search.
    # x - y = ln(exp(x) / exp(y)) = ln(exp(x)) - ln(exp(y))... but we need
    # to express this in pure EML. Using exp and ln as intermediate:
    # x - y = ln(exp(x) / exp(y)) = ln(exp(x) * exp(-y)) = ln(exp(x - y))
    # That's circular. The paper says K=11, meaning 11 RPN tokens.
    # From the bootstrapping chain: subtraction uses exp, ln, and division or
    # negation. Let's use the indirect route through the bootstrapped primitives.

    def eml_sub(x, y):
        """x - y via exp/ln: x - y = ln(exp(x)/exp(y)) = ln(exp(x)) + ln(1/exp(y))
        But more directly: exp and ln give us the exp-log representation.
        x - y = ln(exp(x - y))... circular.
        Actually: x = ln(exp(x)), y = ln(exp(y)), and
        x - y = eml(x, exp(y))  since eml(x, exp(y)) = exp(x) - ln(exp(y)) = exp(x) - y
        That's exp(x) - y, not x - y.

        Let's try: x - y = ln(exp(x) / exp(y)). We need division or negation.
        From paper: negation K=15, subtraction K=11.
        Since sub is simpler, there must be a direct EML path.

        eml(ln(x), exp(y)) = exp(ln(x)) - ln(exp(y)) = x - y  (!!!)
        But ln and exp must themselves be in EML form.
        """
        # Using the identity: eml(ln(x), exp(y)) = x - y
        # where ln and exp are in EML form
        return eml(eml_ln(x), eml_exp(y))

    print("\n--- Subtraction: x - y ---")
    for x, y in zip(x_test[:4], y_test[:4]):
        if x > 0:  # ln(x) needs x > 0 in real domain
            verify(f"{x:.2f} - {y:.2f}", eml_sub(x, y), x - y)

    # Addition: x + y. Paper says K=19 (direct) or K=27 (compiler).
    # x + y = x - (-y), but we need negation.
    # Or: x + y = ln(exp(x) * exp(y)) = ln(exp(x)) + ln(exp(y))
    # In exp-log: x + y = ln(exp(x) * exp(y))
    # We need multiplication... which needs addition. Circular.
    #
    # The bootstrapping chain (Fig. 1) goes:
    # eml,1 -> e -> exp -> ln -> subtraction -> negation -> addition -> ...
    # So negation comes before addition.
    # neg(x) = 0 - x = eml_sub(0, x)

    def eml_neg(x):
        """Negation: -x = 0 - x"""
        zero = const_zero()
        return eml_sub(zero + 0j, x)  # 0 must be expressed in EML too

    print("\n--- Negation: -x ---")
    for x in [0.5, 1.0, np.e, np.pi]:
        verify(f"-{x:.2f}", eml_neg(x), -x, tol=1e-8)

    print("\n--- Addition: x + y = x - (-y) ---")
    def eml_add(x, y):
        return eml_sub(x, eml_neg(y))

    for x, y in zip([0.5, 1.0, np.e], [0.3, 2.0, np.pi]):
        verify(f"{x:.2f} + {y:.2f}", eml_add(x, y), x + y, tol=1e-6)

    # Multiplication: x * y = exp(ln(x) + ln(y)). K=17.
    print("\n--- Multiplication: x * y = exp(ln(x) + ln(y)) ---")
    def eml_mul(x, y):
        return eml_exp(eml_add(eml_ln(x), eml_ln(y)))

    for x, y in zip([0.5, 2.0, np.e], [0.3, 3.0, np.pi]):
        verify(f"{x:.2f} * {y:.2f}", eml_mul(x, y), x * y, tol=1e-4)

    print("\n" + "=" * 90)
    print("NOTE: Accumulated floating-point error grows with composition depth.")
    print("The paper's direct-search K values correspond to optimized EML programs;")
    print("our bootstrapped versions go through more intermediate steps.")
    print("=" * 90)
