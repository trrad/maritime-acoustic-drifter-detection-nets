"""Generate .hex test vectors from the Python LNS8 model.

Each .hex file has one vector per line. Format varies by op:
  Binary ops (MUL/DIV/ADD/SUB): a_sign a_mag b_sign b_mag r_sign r_mag
  Unary ops (EXP/LN):           a_sign a_mag r_sign r_mag
All values are 2-digit hex. Signs are 00 or 01.

Run from rtl/vectors/:
    python gen_test_vectors.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'experiments'))

from importlib import import_module
lns8 = import_module('08_lns_cycle_accurate')

import numpy as np
import random

random.seed(42)
np.random.seed(42)


def sign_to_hw(s):
    """Python sign (+1, -1, 0) → hardware (0=pos, 1=neg)."""
    if s == -1:
        return 1
    return 0  # 0 or +1 → 0


def mag_to_hw(m):
    """Python int8 mag → unsigned 8-bit hex."""
    return m & 0xFF


def hw_to_signed_mag(am):
    """Unsigned 8-bit hw value → Python signed int."""
    return am if am < 128 else am - 256


def hw_to_python(hw_sign, hw_mag):
    """Convert HW encoding to Python (sign, mag) with zero normalization.
    In HW, mag=0x80 with sign=0 is zero. mag=0x80 with sign=1 would be
    -(2^(-8)) but after negation becomes (0,0x80)=zero. Normalize all
    mag=0x80 inputs to zero for consistency."""
    if hw_mag == 0x80:
        return (0, -128)
    py_sign = -1 if hw_sign else 1
    py_mag = hw_to_signed_mag(hw_mag)
    return (py_sign, py_mag)


def normalize_output(py_sign, py_mag):
    """Normalize Python output to match HW encoding.
    If mag=-128 (0x80), it's zero regardless of sign."""
    hw_mag = py_mag & 0xFF
    if hw_mag == 0x80:
        return (0, 0x80)
    return (sign_to_hw(py_sign), hw_mag)


def write_hex(filename, vectors):
    with open(filename, 'w') as f:
        for v in vectors:
            f.write(' '.join(f'{x:02x}' for x in v) + '\n')
    print(f"  {filename}: {len(vectors)} vectors")


def gen_binary_op(op_fn, name, n_sampled=2048):
    """Generate test vectors for a binary op (MUL/DIV/ADD/SUB)."""
    vectors = []

    # Edge cases: zero operands
    edge_mags = [0x80, 0x00, 0x01, 0x7F, 0xFF, 0x81, 0x10, 0xF0]
    edge_signs = [0, 1]

    for as_ in edge_signs:
        for am in edge_mags:
            for bs in edge_signs:
                for bm in edge_mags:
                    # Normalize HW inputs: mag=0x80 → sign=0 (zero)
                    hw_as = 0 if am == 0x80 else as_
                    hw_bs = 0 if bm == 0x80 else bs
                    py_as, py_am = hw_to_python(hw_as, am)
                    py_bs, py_bm = hw_to_python(hw_bs, bm)

                    if name == 'div' and py_bs == 0:
                        continue

                    try:
                        rs, rm, _ = op_fn(py_as, int(py_am), py_bs, int(py_bm))
                        r_sign, r_mag = normalize_output(rs, rm)
                        vectors.append([hw_as, am, hw_bs, bm, r_sign, r_mag])
                    except:
                        pass

    # Random vectors
    for _ in range(n_sampled):
        as_ = random.randint(0, 1)
        am = random.randint(0, 255)
        bs = random.randint(0, 1)
        bm = random.randint(0, 255)

        hw_as = 0 if am == 0x80 else as_
        hw_bs = 0 if bm == 0x80 else bs
        py_as, py_am = hw_to_python(hw_as, am)
        py_bs, py_bm = hw_to_python(hw_bs, bm)

        if name == 'div' and py_bs == 0:
            continue

        try:
            rs, rm, _ = op_fn(py_as, int(py_am), py_bs, int(py_bm))
            r_sign, r_mag = normalize_output(rs, rm)
            vectors.append([hw_as, am, hw_bs, bm, r_sign, r_mag])
        except:
            pass

    return vectors


def gen_unary_op(op_fn, _name):
    """Generate exhaustive test vectors for a unary op (EXP/LN)."""
    vectors = []

    for as_ in [0, 1]:
        for am in range(256):
            hw_as = 0 if am == 0x80 else as_
            py_as, py_am = hw_to_python(hw_as, am)

            try:
                rs, rm, _ = op_fn(py_as, int(py_am))
                r_sign, r_mag = normalize_output(rs, rm)
                vectors.append([hw_as, am, r_sign, r_mag])
            except:
                pass

    return vectors


if __name__ == '__main__':
    print("Generating LNS8 test vectors...")

    vecs = gen_binary_op(lns8.lns8_multiply, 'mul')
    write_hex('mul_vectors.hex', vecs)

    vecs = gen_binary_op(lns8.lns8_divide, 'div')
    write_hex('div_vectors.hex', vecs)

    vecs = gen_binary_op(lns8.lns8_add, 'add')
    write_hex('add_vectors.hex', vecs)

    vecs = gen_binary_op(lns8.lns8_subtract, 'sub')
    write_hex('sub_vectors.hex', vecs)

    vecs = gen_unary_op(lns8.lns8_exp, 'exp')
    write_hex('exp_vectors.hex', vecs)

    vecs = gen_unary_op(lns8.lns8_ln, 'ln')
    write_hex('ln_vectors.hex', vecs)

    print("Done.")
