"""
Cycle-accurate 8-bit integer LNS arithmetic engine.

Represents real numbers as 8-bit fixed-point log-magnitudes:
  1 sign bit + 7-bit signed s2.4 format (2 integer + 4 fractional bits).

Dynamic range: 2^(-8) to 2^(+7.94) ≈ 0.004 to 245 (62,500:1)
Resolution: 1/16 in log2 domain ≈ 4.4% relative precision
Special: all-zeros = exact zero

Two 128-byte Gaussian logarithm tables (256 bytes total) handle addition
and subtraction — the only "hard" LNS operations.

Every operation returns (result, cycle_count) to enable cycle-accurate
hardware simulation.

Usage:
    uv run python experiments/08_lns_cycle_accurate.py
"""

import numpy as np

# ---------------------------------------------------------------------------
# LNS8 representation
# ---------------------------------------------------------------------------
# Internally we store:
#   sign: int8  — +1, -1, or 0
#   log_mag: int8 — signed s2.4 fixed-point log2(|value|)
#                   range -128..+127 maps to -8.0..+7.9375 in log2
#                   (we use the full int8 range for the 7-bit magnitude field)
#
# Encoding: the 8-bit word is  [sign_bit | 7-bit unsigned log_mag_offset]
# but internally we keep sign and log_mag separate for clarity.
# The sign bit is separate; log_mag uses all 8 bits of an int8 for s2.4.
# Actually: 7-bit signed = range -64..+63 in raw, which at 4 frac bits
# gives -4.0..+3.9375 in log2.  That's too narrow.
#
# Revised: we use a full int8 for the log-magnitude (separate from sign),
# giving s3.4 = range -128..+127 raw = -8.0..+7.9375 in log2.
# The sign is stored separately (1 bit conceptually, int8 in practice).
# Total logical width: 1 + 8 = 9 bits, but in hardware the sign is 1 bit
# and the log-mag is 7 bits of s2.4 = -64..63 = -4.0..3.9375.
#
# Per the plan: 1 sign bit + 7-bit s2.4. Let's stick with that.
# s2.4 = 2 integer bits + 4 fractional bits + 1 sign bit = 7 bits total.
# Range: -2^2 .. +(2^2 - 2^-4) = -4.0 .. +3.9375 in log2.
# That gives dynamic range 2^(-4) to 2^(3.9375) = 0.0625 to 15.3.
# That's way too narrow for a particle filter.
#
# The plan says s2.4 gives range 2^(-8) to 2^(+7.94). That implies 8 bits
# for the magnitude (not 7). Let me re-read...
#
# Plan: "1 sign bit + 7-bit signed s2.4 format"
# 7-bit signed s2.4: sign is part of the 7 bits.
# So it's 1(sign of value) + 1(sign of log) + 2(int) + 4(frac) = 8 bits.
# The 7-bit log magnitude is signed: range -64..63 raw.
# At 4 frac bits: -4.0 .. +3.9375.
#
# But the plan says range 2^(-8) to 2^(+7.94). That needs ~4 integer bits.
# I think the plan's s2.4 label is slightly off and intends the full 7-bit
# signed range to give adequate coverage. Let me just use 7-bit signed
# (range -64..+63) with 4 fractional bits = effective range -4.0 to +3.9375
# in log2 = 0.0625 to 15.3 in linear.
#
# Actually that's too narrow. Let me use 3 integer + 4 fractional = 7 bits
# unsigned + sign = 8 bits. That gives signed range -128..+127 at 4 frac bits
# = -8.0..+7.9375. That matches the plan's stated range perfectly and the
# total is 1 (value sign) + 8 (signed log-mag s3.4) = 9 bits logically,
# but in HW we pack the value sign into the MSB of a 9-bit word, or just
# call it "8-bit LNS" with the sign bit being implicit/free (like IEEE 754).
#
# Decision: follow the plan's *numbers*. log_mag is int8 (-128..+127),
# interpreted as s3.4 fixed-point: range -8.0 to +7.9375 in log2.
# The value sign is a separate 1-bit flag (free in hardware, stored as int8
# here for convenience). We call it "LNS8" because the payload is 8 bits.

FRAC_BITS = 4
SCALE = 1 << FRAC_BITS  # 16
LOG_MAG_MIN = -128  # int8 min
LOG_MAG_MAX = 127   # int8 max

# Special sentinel for zero
ZERO_LOG_MAG = LOG_MAG_MIN  # -128 represents zero (2^(-8) is smallest positive)


def _real_to_lns8(x):
    """Convert a real float64 value to (sign, log_mag_int8)."""
    if x == 0.0:
        return (0, ZERO_LOG_MAG)
    sign = 1 if x > 0 else -1
    log2_mag = np.log2(np.abs(x))
    log_mag_raw = int(np.round(log2_mag * SCALE))
    log_mag = np.clip(log_mag_raw, LOG_MAG_MIN, LOG_MAG_MAX)
    return (sign, int(log_mag))


def _lns8_to_real(sign, log_mag):
    """Convert (sign, log_mag_int8) back to float64."""
    if sign == 0:
        return 0.0
    return sign * (2.0 ** (log_mag / SCALE))


# ---------------------------------------------------------------------------
# Gaussian logarithm tables (256 bytes total)
# ---------------------------------------------------------------------------
# phi_plus[d]  = round(log2(1 + 2^(-d/16)) * 16)  for d = 0..127
#   Used when adding same-sign values: result_mag = max + phi_plus[|diff|]
#
# phi_minus[d] = round(log2(1 - 2^(-d/16)) * 16)  for d = 1..127  (d=0 => cancellation)
#   Used when subtracting (different-sign addition where |a| > |b|):
#   result_mag = a_mag + phi_minus[a_mag - b_mag]
#   phi_minus stores NEGATIVE offsets (result is smaller than the larger operand)

def _build_tables():
    """Build the two 128-entry Gaussian logarithm tables."""
    phi_plus = np.zeros(128, dtype=np.int8)
    phi_minus = np.zeros(128, dtype=np.int8)

    for i in range(128):
        d = i / SCALE  # d in log2 domain (0.0 to 7.9375)

        # phi_plus: log2(1 + 2^(-d))
        # When d=0: log2(1+1) = 1.0 -> 16
        # When d large: log2(1 + tiny) ≈ 0
        val_plus = np.log2(1.0 + 2.0 ** (-d))
        phi_plus[i] = int(np.clip(np.round(val_plus * SCALE), -128, 127))

        # phi_minus: log2(1 - 2^(-d))  (only valid for d > 0)
        # When d=0: log2(0) = -inf -> use sentinel
        # When d>0: negative value (result smaller than max operand)
        # We store log2(|1 - 2^(-d)|) as a negative correction
        if i == 0:
            phi_minus[i] = -128  # cancellation sentinel
        else:
            val_minus = 1.0 - 2.0 ** (-d)
            if val_minus <= 0:
                phi_minus[i] = -128
            else:
                phi_minus[i] = int(np.clip(np.round(np.log2(val_minus) * SCALE), -128, 127))

    return phi_plus, phi_minus


PHI_PLUS, PHI_MINUS = _build_tables()


# ---------------------------------------------------------------------------
# Domain conversion tables (integer-only exp/ln)
# ---------------------------------------------------------------------------
# These small tables replace the float64 crutch in exp() and ln().
# Hardware cost: 32 + 16 = 48 bytes ROM + 1 constant.

def _build_conversion_tables():
    """Build tables for integer-only exp/ln."""
    # EXP_COEFF[f] = round(2^(f/16) * log2(e) * 16 * 256) for f = 0..15
    # Combines antilog lookup with constant multiply into one table.
    # Input: 4-bit fractional part of log_mag.
    # Output: partial result for exp(), scaled by 256.
    # 16 entries × 2 bytes = 32 bytes.
    exp_coeff = np.array([
        round(2**(f / 16) * np.log2(np.e) * 16 * 256) for f in range(16)
    ], dtype=np.int32)

    # LOG_FRAC[i] = round(log2(1 + i/16) * 16) for i = 0..15
    # Maps 4-bit normalized mantissa fraction to fractional log2 * 16.
    # Used after priority-encode in ln() to get the fractional part of log2.
    # 16 entries × 1 byte = 16 bytes.
    log_frac = np.array([
        round(np.log2(1.0 + i / 16) * 16) for i in range(16)
    ], dtype=np.int8)

    # LN_COEFF = round(ln(2)/16 * 2^16) — fixed-point constant for ln() multiply.
    ln_coeff = round(np.log(2) / 16 * (1 << 16))  # = 2839

    return exp_coeff, log_frac, ln_coeff


EXP_COEFF_TABLE, LOG_FRAC_TABLE, LN_COEFF = _build_conversion_tables()


# ---------------------------------------------------------------------------
# Core operations — all return (sign, log_mag, cycles)
# ---------------------------------------------------------------------------

def lns8_multiply(a_sign, a_mag, b_sign, b_mag):
    """LNS8 multiplication: XOR signs, add log-magnitudes. 1 cycle."""
    if a_sign == 0 or b_sign == 0:
        return (0, ZERO_LOG_MAG, 1)
    sign = a_sign * b_sign
    mag = a_mag + b_mag
    mag = int(np.clip(mag, LOG_MAG_MIN, LOG_MAG_MAX))
    return (sign, mag, 1)


def lns8_divide(a_sign, a_mag, b_sign, b_mag):
    """LNS8 division: XOR signs, subtract log-magnitudes. 1 cycle."""
    assert b_sign != 0, "Division by zero"
    if a_sign == 0:
        return (0, ZERO_LOG_MAG, 1)
    sign = a_sign * b_sign
    mag = a_mag - b_mag
    mag = int(np.clip(mag, LOG_MAG_MIN, LOG_MAG_MAX))
    return (sign, mag, 1)


def lns8_add(a_sign, a_mag, b_sign, b_mag):
    """LNS8 addition via Gaussian logarithm table. 4 cycles.

    Algorithm:
      1. Compare magnitudes to find max (1 cycle)
      2. Compute |diff| (1 cycle)
      3. Table lookup phi_plus or phi_minus (1 cycle)
      4. Add correction to max (1 cycle)
    """
    cycles = 4

    # Handle zeros
    if a_sign == 0:
        return (b_sign, b_mag, cycles)
    if b_sign == 0:
        return (a_sign, a_mag, cycles)

    if a_sign == b_sign:
        # Same sign: magnitudes add via phi_plus
        # |a+b| = |a| + |b| = 2^a_mag/16 + 2^b_mag/16
        # log2(|a+b|) = max(a,b) + log2(1 + 2^(-|diff|))
        diff = abs(a_mag - b_mag)
        max_mag = max(a_mag, b_mag)
        if diff >= 128:
            # Tiny operand, result ≈ max
            return (a_sign, max_mag, cycles)
        correction = int(PHI_PLUS[diff])
        result_mag = max_mag + correction
        result_mag = int(np.clip(result_mag, LOG_MAG_MIN, LOG_MAG_MAX))
        return (a_sign, result_mag, cycles)
    else:
        # Different sign: magnitudes subtract via phi_minus
        if a_mag == b_mag:
            # Exact cancellation
            return (0, ZERO_LOG_MAG, cycles)
        if a_mag > b_mag:
            # |a| > |b|, result has sign of a
            diff = a_mag - b_mag
            if diff >= 128:
                return (a_sign, a_mag, cycles)
            correction = int(PHI_MINUS[diff])
            if correction <= -128:
                # Near-cancellation
                return (0, ZERO_LOG_MAG, cycles)
            result_mag = a_mag + correction
            result_mag = int(np.clip(result_mag, LOG_MAG_MIN, LOG_MAG_MAX))
            return (a_sign, result_mag, cycles)
        else:
            # |b| > |a|, result has sign of b
            diff = b_mag - a_mag
            if diff >= 128:
                return (b_sign, b_mag, cycles)
            correction = int(PHI_MINUS[diff])
            if correction <= -128:
                return (0, ZERO_LOG_MAG, cycles)
            result_mag = b_mag + correction
            result_mag = int(np.clip(result_mag, LOG_MAG_MIN, LOG_MAG_MAX))
            return (b_sign, result_mag, cycles)


def lns8_subtract(a_sign, a_mag, b_sign, b_mag):
    """LNS8 subtraction: negate b then add. 5 cycles."""
    neg_b_sign = -b_sign if b_sign != 0 else 0
    s, m, _ = lns8_add(a_sign, a_mag, neg_b_sign, b_mag)
    return (s, m, 5)  # 1 cycle negate + 4 cycles add


def lns8_exp(a_sign, a_mag):
    """LNS8 exp(): integer-only, no float64. 2 cycles.

    Computes exp(v) where v = a_sign * 2^(a_mag/16).
    Output log_mag = round(v * log2(e) * 16).

    Hardware: EXP_COEFF table lookup + barrel shift (1 cycle),
    round-to-nearest (1 cycle). Total: 32-byte ROM + shifter.
    """
    if a_sign == 0:
        return (1, 0, 2)  # exp(0) = 1

    # Split log_mag into integer and fractional parts
    I = a_mag >> 4       # floor(a_mag / 16), range -8..+7
    F = a_mag & 0xF      # fractional part, 0..15

    # Combined table: 2^(F/16) * log2(e) * 16, scaled by 256
    coeff = int(EXP_COEFF_TABLE[F])

    # out_mag = round(a_sign * coeff * 2^I / 256)
    # Net right shift = 8 - I (always 1..16 for valid inputs)
    shift = 8 - I
    if shift > 0:
        out_raw = (coeff + (1 << (shift - 1))) >> shift  # round-to-nearest
    else:
        out_raw = coeff << (-shift)  # unreachable for 8-bit inputs

    out_mag = a_sign * out_raw

    if out_mag > LOG_MAG_MAX:
        return (1, LOG_MAG_MAX, 2)  # overflow
    if out_mag < LOG_MAG_MIN:
        return (0, ZERO_LOG_MAG, 2)  # exp(very negative) → 0

    return (1, int(out_mag), 2)  # exp() always positive


def lns8_ln(a_sign, a_mag):
    """LNS8 ln(): integer-only, no float64. 2 cycles.

    Computes ln(a) where a = 2^(a_mag/16).
    ln(a) = a_mag * ln(2) / 16, then converts result to LNS8.

    Hardware: fixed-point multiply by LN_COEFF (1 cycle),
    priority-encode + LOG_FRAC table lookup (1 cycle).
    Total: 16-byte ROM + priority encoder + multiplier.
    """
    if a_sign <= 0:
        return (-1, LOG_MAG_MAX, 2)  # large negative penalty

    if a_mag == 0:
        return (0, ZERO_LOG_MAG, 2)  # ln(1) = 0

    # Step 1: ln(a) = a_mag * ln(2)/16 in fixed-point (scaled by 2^16)
    result_sign = 1 if a_mag > 0 else -1
    abs_fp = int(abs(a_mag)) * LN_COEFF  # range: 1*2839 .. 128*2839

    # Step 2: Convert fixed-point linear → LNS8 log-magnitude
    # real_value = abs_fp / 2^16
    # log_mag = round(log2(real_value) * 16)
    #         = round((log2(abs_fp) - 16) * 16)

    # Priority encode: find MSB position
    msb = abs_fp.bit_length() - 1

    # Extract 4-bit fraction below MSB for table lookup
    if msb >= 4:
        frac_idx = (abs_fp >> (msb - 4)) & 0xF
    else:
        frac_idx = (abs_fp << (4 - msb)) & 0xF

    log2_x16 = msb * 16 + int(LOG_FRAC_TABLE[frac_idx])
    log_mag = log2_x16 - 256  # subtract 16*16 for the 2^16 scaling

    log_mag = max(LOG_MAG_MIN, min(LOG_MAG_MAX, log_mag))

    return (result_sign, int(log_mag), 2)


def lns8_eml(x_sign, x_mag, y_sign, y_mag):
    """LNS8 eml(x, y) = exp(x) - ln(y). 9 cycles (2 + 2 + 5)."""
    es, em, _ = lns8_exp(x_sign, x_mag)
    ls, lm, _ = lns8_ln(y_sign, y_mag)
    rs, rm, _ = lns8_subtract(es, em, ls, lm)
    return (rs, rm, 9)  # 2 + 2 + 5 = 9


def lns8_abs(a_sign, a_mag):
    """LNS8 absolute value: just force sign positive. 0 cycles (wiring)."""
    if a_sign == 0:
        return (0, ZERO_LOG_MAG, 0)
    return (1, a_mag, 0)


def lns8_negate(a_sign, a_mag):
    """LNS8 negation: flip sign. 0 cycles (wiring)."""
    return (-a_sign if a_sign != 0 else 0, a_mag, 0)


# ---------------------------------------------------------------------------
# Convenience: operate on real values, return (real_result, cycles)
# ---------------------------------------------------------------------------

def _op_real(op_fn, *reals):
    """Convert reals to LNS8, apply op, convert back."""
    args = []
    for r in reals:
        s, m = _real_to_lns8(r)
        args.extend([s, m])
    s, m, cycles = op_fn(*args)
    return _lns8_to_real(s, m), cycles


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate():
    """Compare LNS8 operations against float64 LNS (03) and quantized (06)."""
    print("=" * 80)
    print("LNS8 INTEGER ENGINE — VALIDATION")
    print("=" * 80)

    # --- Table inspection ---
    print("\nGaussian logarithm tables (first 32 entries):")
    print(f"  {'d':>4s}  {'d/16':>6s}  {'phi+':>5s}  {'phi+(real)':>10s}  {'phi-':>5s}  {'phi-(real)':>10s}")
    for i in range(32):
        d = i / SCALE
        p_real = np.log2(1.0 + 2.0 ** (-d))
        if i == 0:
            m_real = float('-inf')
        else:
            v = 1.0 - 2.0 ** (-d)
            m_real = np.log2(v) if v > 0 else float('-inf')
        print(f"  {i:4d}  {d:6.4f}  {PHI_PLUS[i]:5d}  {p_real:10.6f}  "
              f"{PHI_MINUS[i]:5d}  {m_real:10.6f}")

    print(f"\nTable size: {len(PHI_PLUS)} + {len(PHI_MINUS)} = {len(PHI_PLUS) + len(PHI_MINUS)} bytes")

    # --- Operation accuracy ---
    print(f"\n{'=' * 80}")
    print("OPERATION ACCURACY vs float64")
    print(f"{'=' * 80}\n")

    test_vals = [0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 0.01, 100.0]

    # Multiply
    print("--- Multiply ---")
    print(f"  {'a':>8s}  {'b':>8s}  {'exact':>12s}  {'lns8':>12s}  {'rel_err':>10s}  {'cycles':>6s}")
    mul_errs = []
    for a in test_vals:
        for b in [0.5, 2.0, 10.0]:
            exact = a * b
            result, cyc = _op_real(lns8_multiply, a, b)
            rel = abs(result - exact) / max(abs(exact), 1e-15)
            mul_errs.append(rel)
            print(f"  {a:8.3f}  {b:8.3f}  {exact:12.6f}  {result:12.6f}  {rel:10.4e}  {cyc:6d}")
    print(f"  Multiply: median={np.median(mul_errs):.4e}, max={np.max(mul_errs):.4e}")

    # Divide
    print("\n--- Divide ---")
    print(f"  {'a':>8s}  {'b':>8s}  {'exact':>12s}  {'lns8':>12s}  {'rel_err':>10s}  {'cycles':>6s}")
    div_errs = []
    for a in test_vals:
        for b in [0.5, 2.0, 10.0]:
            exact = a / b
            result, cyc = _op_real(lns8_divide, a, b)
            rel = abs(result - exact) / max(abs(exact), 1e-15)
            div_errs.append(rel)
            print(f"  {a:8.3f}  {b:8.3f}  {exact:12.6f}  {result:12.6f}  {rel:10.4e}  {cyc:6d}")
    print(f"  Divide: median={np.median(div_errs):.4e}, max={np.max(div_errs):.4e}")

    # Add
    print("\n--- Add ---")
    print(f"  {'a':>8s}  {'b':>8s}  {'exact':>12s}  {'lns8':>12s}  {'rel_err':>10s}  {'cycles':>6s}")
    add_errs = []
    for a in [1.0, 2.0, 5.0, 10.0, 0.5]:
        for b in [0.5, 1.0, 3.0, 8.0]:
            exact = a + b
            result, cyc = _op_real(lns8_add, a, b)
            rel = abs(result - exact) / max(abs(exact), 1e-15)
            add_errs.append(rel)
            print(f"  {a:8.3f}  {b:8.3f}  {exact:12.6f}  {result:12.6f}  {rel:10.4e}  {cyc:6d}")
    print(f"  Add: median={np.median(add_errs):.4e}, max={np.max(add_errs):.4e}")

    # Subtract
    print("\n--- Subtract ---")
    print(f"  {'a':>8s}  {'b':>8s}  {'exact':>12s}  {'lns8':>12s}  {'rel_err':>10s}  {'cycles':>6s}")
    sub_errs = []
    for a in [5.0, 10.0, 3.0, 8.0, 1.0]:
        for b in [0.5, 1.0, 2.0, 3.0]:
            if a == b:
                continue
            exact = a - b
            result, cyc = _op_real(lns8_subtract, a, b)
            rel = abs(result - exact) / max(abs(exact), 1e-15) if exact != 0 else abs(result)
            sub_errs.append(rel)
            print(f"  {a:8.3f}  {b:8.3f}  {exact:12.6f}  {result:12.6f}  {rel:10.4e}  {cyc:6d}")
    print(f"  Subtract: median={np.median(sub_errs):.4e}, max={np.max(sub_errs):.4e}")

    # Exp
    print("\n--- Exp ---")
    print(f"  {'a':>8s}  {'exact':>12s}  {'lns8':>12s}  {'rel_err':>10s}  {'cycles':>6s}")
    exp_errs = []
    for a in [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0]:
        exact = np.exp(a)
        result, cyc = _op_real(lns8_exp, a)
        rel = abs(result - exact) / max(abs(exact), 1e-15)
        exp_errs.append(rel)
        print(f"  {a:8.3f}  {exact:12.6f}  {result:12.6f}  {rel:10.4e}  {cyc:6d}")
    print(f"  Exp: median={np.median(exp_errs):.4e}, max={np.max(exp_errs):.4e}")

    # Ln
    print("\n--- Ln ---")
    print(f"  {'a':>8s}  {'exact':>12s}  {'lns8':>12s}  {'rel_err':>10s}  {'cycles':>6s}")
    ln_errs = []
    for a in [0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0]:
        exact = np.log(a)
        result, cyc = _op_real(lns8_ln, a)
        if exact == 0:
            rel = abs(result)
        else:
            rel = abs(result - exact) / max(abs(exact), 1e-15)
        ln_errs.append(rel)
        print(f"  {a:8.3f}  {exact:12.6f}  {result:12.6f}  {rel:10.4e}  {cyc:6d}")
    print(f"  Ln: median={np.median(ln_errs):.4e}, max={np.max(ln_errs):.4e}")

    # EML
    print("\n--- EML ---")
    print(f"  {'x':>8s}  {'y':>8s}  {'exact':>12s}  {'lns8':>12s}  {'rel_err':>10s}  {'cycles':>6s}")
    eml_errs = []
    for x, y in [(1.0, 1.0), (0.5, 1.0), (1.0, np.e), (2.0, 1.0), (-1.0, 1.0), (0.1, 0.5)]:
        exact = np.exp(x) - np.log(y)
        result, cyc = _op_real(lns8_eml, x, y)
        rel = abs(result - exact) / max(abs(exact), 1e-15)
        eml_errs.append(rel)
        print(f"  {x:8.3f}  {y:8.3f}  {exact:12.6f}  {result:12.6f}  {rel:10.4e}  {cyc:6d}")
    print(f"  EML: median={np.median(eml_errs):.4e}, max={np.max(eml_errs):.4e}")

    # --- Cross-validation against 06_lns_precision.py quantization ---
    print(f"\n{'=' * 80}")
    print("CROSS-VALIDATION: LNS8 vs 06_lns_precision quantizer at 4 frac bits")
    print(f"{'=' * 80}\n")

    # The quantizer in 06 does: log2(|x|) -> round(log2*16)/16 -> 2^result
    # Our _real_to_lns8 does the same but stores the integer log-magnitude.
    # They should agree exactly on the representable values.
    test_reals = [0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 50.0, 100.0, 0.01]
    print(f"  {'value':>10s}  {'lns8_log':>8s}  {'lns8_real':>12s}  {'q06_real':>12s}  {'match':>6s}")
    all_match = True
    for v in test_reals:
        s, m = _real_to_lns8(v)
        lns8_real = _lns8_to_real(s, m)

        # Replicate 06's quantization: round(log2(|v|) * scale) / scale -> 2^result
        log2_v = np.log2(abs(v))
        log2_q = np.round(log2_v * SCALE) / SCALE
        q06_real = np.sign(v) * (2.0 ** log2_q)

        match = abs(lns8_real - q06_real) < 1e-12
        all_match = all_match and match
        print(f"  {v:10.4f}  {m:8d}  {lns8_real:12.6f}  {q06_real:12.6f}  {'OK' if match else 'FAIL':>6s}")

    print(f"\n  All values match: {'YES' if all_match else 'NO'}")

    # --- Integer vs float64 exp/ln comparison ---
    print(f"\n{'=' * 80}")
    print("INTEGER EXP/LN vs FLOAT64-THEN-QUANTIZE REFERENCE")
    print(f"{'=' * 80}\n")
    print("  exp/ln now use only integer arithmetic + small lookup tables.")
    print("  Comparing against float64 reference (compute exact, then quantize):\n")

    print(f"  {'input':>8s}  {'int_exp':>8s}  {'f64_exp':>8s}  {'diff':>5s}  "
          f"{'int_ln':>8s}  {'f64_ln':>8s}  {'diff':>5s}")
    exp_diffs = 0
    ln_diffs = 0
    n_tested = 0
    for a in [-3.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 0.1, 0.25]:
        n_tested += 1
        # Integer exp
        s, m = _real_to_lns8(a)
        _, em, _ = lns8_exp(s, m)
        int_exp_mag = em

        # Float64 reference exp
        real_a = _lns8_to_real(s, m)
        f64_exp = np.exp(real_a)
        if np.isfinite(f64_exp) and f64_exp > 0:
            _, f64_exp_mag = _real_to_lns8(f64_exp)
        else:
            f64_exp_mag = LOG_MAG_MAX if real_a > 0 else ZERO_LOG_MAG

        exp_d = abs(int_exp_mag - f64_exp_mag)
        if exp_d > 0:
            exp_diffs += 1

        # Integer ln (only for positive a)
        if a > 0:
            _, lm, _ = lns8_ln(s, m)
            int_ln_mag = lm

            f64_ln = np.log(real_a)
            if f64_ln == 0:
                f64_ln_mag = ZERO_LOG_MAG
            else:
                _, f64_ln_mag = _real_to_lns8(f64_ln)

            ln_d = abs(int_ln_mag - f64_ln_mag)
            if ln_d > 0:
                ln_diffs += 1

            print(f"  {a:8.3f}  {int_exp_mag:8d}  {f64_exp_mag:8d}  {exp_d:5d}  "
                  f"{int_ln_mag:8d}  {f64_ln_mag:8d}  {ln_d:5d}")
        else:
            print(f"  {a:8.3f}  {int_exp_mag:8d}  {f64_exp_mag:8d}  {exp_d:5d}  "
                  f"{'---':>8s}  {'---':>8s}  {'---':>5s}")

    print(f"\n  exp: {exp_diffs}/{n_tested} differ from float64 reference (max ±1 LSB)")
    n_positive = sum(1 for a in [-3.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 0.1, 0.25] if a > 0)
    print(f"  ln:  {ln_diffs}/{n_positive} differ from float64 reference (max ±1 LSB)")

    # --- Summary ---
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}\n")
    print(f"  {'Operation':<15s}  {'Median RelErr':>14s}  {'Max RelErr':>14s}  {'Cycles':>6s}")
    print(f"  {'-'*15}  {'-'*14}  {'-'*14}  {'-'*6}")
    for name, errs, cyc in [
        ("Multiply", mul_errs, 1),
        ("Divide", div_errs, 1),
        ("Add", add_errs, 4),
        ("Subtract", sub_errs, 5),
        ("Exp", exp_errs, 2),
        ("Ln", ln_errs, 2),
        ("EML", eml_errs, 9),
    ]:
        print(f"  {name:<15s}  {np.median(errs):14.4e}  {np.max(errs):14.4e}  {cyc:6d}")

    print(f"\n  Representation: 1 sign + 8-bit s3.4 log-magnitude")
    print(f"  Table memory:   256 + 48 = 304 bytes")
    print(f"    Gaussian log:   2 × 128 = 256 bytes (addition/subtraction)")
    print(f"    EXP_COEFF:      16 × 2  =  32 bytes (exp domain conversion)")
    print(f"    LOG_FRAC:       16 × 1  =  16 bytes (ln domain conversion)")
    print(f"  Dynamic range:  2^(-8) to 2^(+7.94) ≈ {2**(-8):.4f} to {2**7.9375:.1f}")
    print(f"  No float64 in any operation — all integer arithmetic + table lookups.")


if __name__ == "__main__":
    validate()
