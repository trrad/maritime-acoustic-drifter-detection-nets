"""Convert RTL trace dump to dashboard-compatible JSON-lines.

Reads:
  - build/rtl_trace.txt        (RTL 6D particle states per timestep)
  - vectors/scenario_sensors.hex (sensor measurements)

Writes JSON-lines to stdout (pipe to dashboard or save to file).

Trace format (from tb_pf_e2e.v):
  STEP t predict=N weight=N resample=N
  TRUTH s0 m0 s1 m1 s2 m2 s3 m3 s4 m4 s5 m5
  P idx s0 m0 s1 m1 s2 m2 s3 m3 s4 m4 s5 m5
  ...

Usage:
    cd rtl && python vectors/rtl_to_jsonl.py > build/rtl_trace.jsonl
    # or pipe to dashboard:
    python vectors/rtl_to_jsonl.py | python ../experiments/11_pf_dashboard.py
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'experiments'))
from importlib import import_module
lns8 = import_module('08_lns_cycle_accurate')

_lns8_to_real = lns8._lns8_to_real

DIM_NAMES = ['x', 'y', 'z', 'vx', 'vy', 'vz']
SENSOR_NAMES = ['GPS-x', 'GPS-y', 'Baro-z']


def hw_to_real(hw_sign, hw_mag):
    """HW encoding → real value. sign: 0=positive, 1=negative."""
    if hw_mag == 0x80:
        return 0.0
    py_sign = -1 if hw_sign else 1
    py_mag = hw_mag if hw_mag < 128 else hw_mag - 256
    return _lns8_to_real(py_sign, py_mag)


def parse_rtl_trace(trace_path):
    """Parse RTL 6D trace file into list of timestep records."""
    steps = []
    current = None

    with open(trace_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            if line.startswith('STEP'):
                if current is not None:
                    steps.append(current)
                parts = line.split()
                t = int(parts[1])
                predict = int(parts[2].split('=')[1])
                weight = int(parts[3].split('=')[1])
                resample = int(parts[4].split('=')[1])
                estimate = 0
                if len(parts) > 5:
                    estimate = int(parts[5].split('=')[1])
                current = {
                    't': t,
                    'predict': predict,
                    'weight': weight,
                    'resample': resample,
                    'estimate': estimate,
                    'truth': [],      # list of (sign, mag) × 6
                    'est_hw': [],     # list of (sign, mag) × 6 from estimator
                    'refs': [],       # list of signed FP × 3 (reference positions)
                    'particles': [],  # list of [(sign, mag) × 6]
                }
            elif line.startswith('TRUTH'):
                parts = line.split()
                vals = parts[1:]
                for d in range(6):
                    s = int(vals[d * 2])
                    m = int(vals[d * 2 + 1])
                    current['truth'].append((s, m))
            elif line.startswith('EST '):
                parts = line.split()
                vals = parts[1:]
                for d in range(6):
                    s = int(vals[d * 2])
                    m = int(vals[d * 2 + 1])
                    current['est_hw'].append((s, m))
            elif line.startswith('REFS '):
                parts = line.split()
                for v in parts[1:]:
                    current['refs'].append(int(v))
            elif line.startswith('P '):
                parts = line.split()
                vals = parts[2:]  # skip "P" and idx
                dims = []
                idx = 0
                for d in range(6):
                    if idx < len(vals) and vals[idx] == 'F':
                        # Position dim: 16-bit signed FP value
                        fp_val = int(vals[idx + 1])
                        dims.append(('F', fp_val))
                        idx += 2
                    elif idx < len(vals) and vals[idx] == 'L':
                        # Velocity dim: LNS8 sign + mag
                        s = int(vals[idx + 1])
                        m = int(vals[idx + 2])
                        dims.append(('L', s, m))
                        idx += 3
                    else:
                        # Legacy format: sign mag (no tag)
                        s = int(vals[idx])
                        m = int(vals[idx + 1])
                        dims.append(('L', s, m))
                        idx += 2
                current['particles'].append(dims)

    if current is not None:
        steps.append(current)

    return steps


def load_sensor_data(sensors_path, n_sensors=3):
    """Load sensor measurements from hex file."""
    sensor_raw = []
    with open(sensors_path) as f:
        for line in f:
            tokens = line.strip().split()
            row = []
            for i in range(0, len(tokens), 2):
                row.append((int(tokens[i], 16), int(tokens[i + 1], 16)))
            sensor_raw.append(row)
    return sensor_raw


def main():
    trace_path = 'build/rtl_trace.txt'
    sensors_path = 'vectors/scenario_sensors.hex'

    if not os.path.exists(trace_path):
        print(f"ERROR: {trace_path} not found. Run E2E simulation first.",
              file=sys.stderr)
        sys.exit(1)

    steps = parse_rtl_trace(trace_path)

    # Load sensor data
    sensor_raw = []
    if os.path.exists(sensors_path):
        sensor_raw = load_sensor_data(sensors_path)

    sum_sq_err = {d: 0.0 for d in DIM_NAMES}

    for step in steps:
        t = step['t']

        # Ground truth (6 dims)
        truth_dict = {}
        for d in range(6):
            if d < len(step['truth']):
                ts, tm = step['truth'][d]
                truth_dict[DIM_NAMES[d]] = round(hw_to_real(ts, tm), 4)
            else:
                truth_dict[DIM_NAMES[d]] = 0.0

        # Particle positions (6 dims per particle)
        # Position dims (0,1,2): stored as FP offset, add ref for absolute
        # Velocity dims (3,4,5): stored as LNS8
        particles_dict = {d: [] for d in DIM_NAMES}
        refs = step.get('refs', [0, 0, 0])
        for p_dims in step['particles']:
            for d in range(6):
                if d < len(p_dims):
                    entry = p_dims[d]
                    if entry[0] == 'F':
                        # FP-stored position dim: signed 16-bit FP (8.8)
                        fp_val = entry[1]
                        val = fp_val / 256.0
                        if d < 3 and d < len(refs):
                            val += refs[d] / 256.0
                    elif entry[0] == 'L':
                        # LNS8-stored velocity dim
                        val = hw_to_real(entry[1], entry[2])
                    else:
                        val = 0.0
                    particles_dict[DIM_NAMES[d]].append(round(val, 4))
                else:
                    particles_dict[DIM_NAMES[d]].append(0.0)

        # Hardware weighted estimate (from estimator, or fallback to mean)
        est_dict = {}
        if step['est_hw']:
            for d in range(6):
                es, em = step['est_hw'][d]
                est_dict[DIM_NAMES[d]] = round(hw_to_real(es, em), 4)
        else:
            # Fallback: unweighted post-resample mean
            n_p = len(step['particles'])
            for d in range(6):
                dn = DIM_NAMES[d]
                if particles_dict[dn]:
                    est_dict[dn] = round(sum(particles_dict[dn]) / n_p, 4)
                else:
                    est_dict[dn] = 0.0

        # Running RMSE
        rmse_dict = {}
        for d in range(6):
            dn = DIM_NAMES[d]
            sum_sq_err[dn] += (est_dict[dn] - truth_dict[dn]) ** 2
            rmse_dict[dn] = round((sum_sq_err[dn] / (t + 1)) ** 0.5, 4)

        # Sensors
        sensor_dict = {}
        if t < len(sensor_raw):
            for j, (ss, sm) in enumerate(sensor_raw[t]):
                name = SENSOR_NAMES[j] if j < len(SENSOR_NAMES) else f's{j}'
                sensor_dict[name] = round(hw_to_real(ss, sm), 4)

        est_cyc = step.get('estimate', 0)
        cycles = {
            'predict': step['predict'],
            'weight': step['weight'],
            'resample': step['resample'],
            'estimate': est_cyc,
            'total': step['predict'] + step['weight'] + step['resample'] + est_cyc,
        }

        # Reference positions for dashboard
        refs_dict = {}
        if refs:
            for d in range(min(3, len(refs))):
                refs_dict[DIM_NAMES[d]] = round(refs[d] / 256.0, 4)

        record = {
            "t": t,
            "truth": truth_dict,
            "estimate": est_dict,
            "particles": particles_dict,
            "weights": [0.0] * len(step['particles']),
            "sensors": sensor_dict,
            "cycles": cycles,
            "rmse": rmse_dict,
            "method": "rtl_lns8_delta",
        }
        if refs_dict:
            record["refs"] = refs_dict

        print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
