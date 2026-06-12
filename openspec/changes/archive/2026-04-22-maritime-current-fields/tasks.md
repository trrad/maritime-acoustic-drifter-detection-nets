## 1. CurrentField Protocol and Configuration — Tests

- [x] 1.1 Protocol satisfaction test — a test double implementing `velocity_at` satisfies `CurrentField` without inheritance
      (tests/maritime/test_current_fields.py)

- [x] 1.2 FieldConfig dataclass construction — `EddySpec` and `FieldConfig` construct with expected defaults and accept custom parameters
      (tests/maritime/test_current_fields.py)

## 2. CurrentField Protocol and Configuration — Implementation

- [x] 2.1 `CurrentField` protocol with `velocity_at(lat_deg, lon_deg, t_sec) -> tuple[float, float]` — typing.Protocol, no methods beyond the protocol definition
      (rtl/vectors/maritime/current_fields.py)

- [x] 2.2 `EddySpec` and `FieldConfig` dataclasses — `EddySpec` with center, radius_m, peak_velocity_ms, cyclonic; `FieldConfig` with mean flow, eddies list, tidal parameters
      (rtl/vectors/maritime/current_fields.py)

## 3. Synthetic Eddy Field — Tests

- [x] 3.1 Mean flow only — velocity_at returns exactly (mean_vx, mean_vy) at any position/time with no eddies or tide
      (tests/maritime/test_current_fields.py)

- [x] 3.2 Eddy tangential velocity at r = sigma matches peak × decay factor within 0.01 m/s
      (tests/maritime/test_current_fields.py)

- [x] 3.3 Velocity at eddy center is zero from eddy contribution — total velocity equals mean + tide only
      (tests/maritime/test_current_fields.py)

- [x] 3.4 Eddy velocity is monotonically non-increasing from center to 3× radius — test at 0.5×, 1.0×, 2.0×, 3.0× radius, verify magnitudes are non-increasing
      (tests/maritime/test_current_fields.py)

- [x] 3.5 M2 tide oscillation period — tidal velocity at quarter period matches amplitude within 0.01 m/s
      (tests/maritime/test_current_fields.py)

- [x] 3.6 Velocity magnitude bounded for typical parameters — 100 random queries with 3 eddies, all magnitudes < 2.0 m/s
      (tests/maritime/test_current_fields.py)

- [x] 3.7 Advection in mean flow — 60s Euler integration at 1 Hz produces displacement within 5 m of analytical (0.1 m/s × 60s = 6 m east)
      (tests/maritime/test_current_fields.py)

- [x] 3.8 Advection bounded in eddy field — 300s integration starting at eddy edge stays within 2× eddy radius
      (tests/maritime/test_current_fields.py)

## 4. Synthetic Eddy Field — Implementation

- [x] 4.1 `SyntheticEddyField.__init__` stores config and precomputes eddy parameters — converts eddy centers to ENU reference frame for distance calculations
      (rtl/vectors/maritime/current_fields.py)

- [x] 4.2 `SyntheticEddyField.velocity_at` computes superposition of mean + eddies + tide — uses haversine distance for eddy radial distance, Gaussian tangential velocity, sinusoidal tide
      (rtl/vectors/maritime/current_fields.py)

## 5. Verification

- [x] 5.1 `uv run pytest tests/maritime/test_current_fields.py` passes with zero failures
- [x] 5.2 Frozen baseline intact — `git diff` shows zero modifications to existing files
- [x] 5.3 Advection accuracy < 5 m over 60 s in mean flow
- [x] 5.4 Eddy velocity profile matches analytical Gaussian to within 0.01 m/s
