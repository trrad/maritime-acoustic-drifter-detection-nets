## 1. SensorSpec — Tests

- [x] 1.1 Valid sensor spec constructs with expected field values and is immutable — `name`, `observed_dim`, `noise_sigma`, `noise_unit`, `max_rate_hz`, `duty_cycle`, `avg_power_mw` round-trip; mutation attempts raise FrozenInstanceError
      (tests/maritime/test_platform_profile.py)

- [x] 1.2 Negative `noise_sigma` is rejected with `ValueError` naming the field
      (tests/maritime/test_platform_profile.py)

- [x] 1.3 Duty cycle outside [0, 1] is rejected — both `duty_cycle=-0.01` and `duty_cycle=1.5` raise `ValueError`
      (tests/maritime/test_platform_profile.py)

- [x] 1.4 Zero or negative `max_rate_hz` is rejected with `ValueError`
      (tests/maritime/test_platform_profile.py)

## 2. SensorSpec — Implementation

- [x] 2.1 `SensorSpec` frozen dataclass with named fields and `__post_init__` enforcing sigma ≥ 0, 0 ≤ duty_cycle ≤ 1, max_rate_hz > 0, avg_power_mw ≥ 0
      (rtl/vectors/maritime/platform_profile.py)

## 3. CommsProfile — Tests

- [x] 3.1 Valid comms profile constructs with expected fields and is immutable — fields round-trip (including `packet_loss_rate`)
      (tests/maritime/test_platform_profile.py)

- [x] 3.2 Slot length exceeding TDMA period is rejected — `slot_length_sec=10.0, tdma_period_sec=5.0` raises `ValueError`
      (tests/maritime/test_platform_profile.py)

- [x] 3.3 Non-positive `max_range_m` is rejected with `ValueError`
      (tests/maritime/test_platform_profile.py)

- [x] 3.4 `packet_loss_rate` outside [0, 1] is rejected — both `packet_loss_rate=-0.01` and `packet_loss_rate=1.5` raise `ValueError`
      (tests/maritime/test_platform_profile.py)

## 4. CommsProfile — Implementation

- [x] 4.1 `CommsProfile` frozen dataclass with `__post_init__` enforcing 0 < slot_length_sec ≤ tdma_period_sec, max_range_m > 0, ranging_sigma_m ≥ 0, packet_bits ≥ 0, 0 ≤ packet_loss_rate ≤ 1, avg_power_mw ≥ 0
      (rtl/vectors/maritime/platform_profile.py)

## 5. ComputeBudget — Tests

- [x] 5.1 Budget within capacity is accepted — `clock_mhz=6, cycles_per_step=33000, pf_update_rate_hz=1.0, headroom=0.8` constructs successfully
      (tests/maritime/test_platform_profile.py)

- [x] 5.2 Budget exceeding capacity is rejected with `ValueError` — `clock_mhz=1, cycles_per_step=2_000_000, pf_update_rate_hz=1.0, headroom=0.8` raises and the message names the overshoot
      (tests/maritime/test_platform_profile.py)

- [x] 5.3 Non-positive `clock_mhz` raises `ValueError`
      (tests/maritime/test_platform_profile.py)

- [x] 5.4 Headroom outside (0, 1] is rejected — both `headroom=0` and `headroom=1.5` raise `ValueError`
      (tests/maritime/test_platform_profile.py)

## 6. ComputeBudget — Implementation

- [x] 6.1 `ComputeBudget` frozen dataclass with `__post_init__` enforcing clock_mhz > 0, cycles_per_step > 0, pf_update_rate_hz > 0, 0 < headroom ≤ 1, and the capacity inequality `cycles_per_step × pf_update_rate_hz ≤ clock_mhz × 1e6 × headroom`
      (rtl/vectors/maritime/platform_profile.py)

## 7. NodeProfile — Tests

- [x] 7.1 Valid node profile constructs with expected state_dim, class_name, sensors tuple, comms, compute, `total_power_budget_mw`, `has_pump`, `ballast_capacity_ml`, `is_moored`, `has_satellite_uplink` — all fields round-trip, profile is immutable, `total_avg_power_mw` equals sum of sensor+comms+compute averages
      (tests/maritime/test_platform_profile.py)

- [x] 7.2 Duplicate sensor names raise `ValueError` — two `SensorSpec` values both named `"imu"` rejected at construction
      (tests/maritime/test_platform_profile.py)

- [x] 7.3 Power overshoot raises `ValueError` — sum of average powers 10 mW vs. budget 5 mW rejected with message naming the overshoot
      (tests/maritime/test_platform_profile.py)

- [x] 7.4 Sensor lookup by name — `profile.sensor("gps")` returns the matching spec; `profile.sensor("nonexistent")` raises `KeyError`
      (tests/maritime/test_platform_profile.py)

- [x] 7.5 Non-positive state_dim raises `ValueError`
      (tests/maritime/test_platform_profile.py)

- [x] 7.6 Pump-ballast consistency is enforced — `has_pump=False` with `ballast_capacity_ml=30.0` raises `ValueError`; `has_pump=False, ballast=0.0` and `has_pump=True, ballast=30.0` both succeed
      (tests/maritime/test_platform_profile.py)

- [x] 7.7 Negative ballast_capacity_ml raises `ValueError`
      (tests/maritime/test_platform_profile.py)

## 8. NodeProfile — Implementation

- [x] 8.1 `NodeProfile` frozen dataclass composing `class_name`, `state_dim`, `sensors: tuple[SensorSpec, ...]`, `comms`, `compute`, `total_power_budget_mw`, `has_pump`, `ballast_capacity_ml`, `is_moored`, `has_satellite_uplink` with `__post_init__` validation (including the pump/ballast invariant) and a `sensor(name)` lookup helper + `total_sensor_power_mw` / `total_avg_power_mw` properties
      (rtl/vectors/maritime/platform_profile.py)

## 9. Bundled M1 Profiles — Tests

- [x] 9.1 State dims match the buoy design — `ANCHOR_PROFILE.state_dim == 25`, `BALLAST_DRIFTER_PROFILE.state_dim == 21`, `PURE_DRIFTER_PROFILE.state_dim == 15`
      (tests/maritime/test_platform_profile.py)

- [x] 9.2 Skeuomorphic sensor presence — anchor has exactly one `"gps"` sensor, ballast drifter and pure drifter have none
      (tests/maritime/test_platform_profile.py)

- [x] 9.3 Power budgets — `PURE_DRIFTER_PROFILE.total_power_budget_mw ≤ 2.0`, `BALLAST_DRIFTER_PROFILE.total_power_budget_mw ≤ 5.0`, `ANCHOR_PROFILE.total_power_budget_mw ≤ 50.0`, and each profile's `total_avg_power_mw ≤ total_power_budget_mw`
      (tests/maritime/test_platform_profile.py)

- [x] 9.4 `ALL_M1_PROFILES` tuple contains the three profiles in order (anchor, ballast_drifter, pure_drifter)
      (tests/maritime/test_platform_profile.py)

- [x] 9.5 Each bundled profile round-trips through construction (i.e., its own invariants hold) — explicit instantiation of a copy via dataclass.replace succeeds
      (tests/maritime/test_platform_profile.py)

- [x] 9.6 Pump discriminator distinguishes non-anchor classes — `ANCHOR_PROFILE.has_pump == False`, `BALLAST_DRIFTER_PROFILE.has_pump == True` with `ballast_capacity_ml > 0`, `PURE_DRIFTER_PROFILE.has_pump == False` with `ballast_capacity_ml == 0.0`
      (tests/maritime/test_platform_profile.py)

- [x] 9.7 Anchor is the only moored, satellite-equipped profile — `ANCHOR_PROFILE.is_moored == True` and `has_satellite_uplink == True`; both flags are `False` for `BALLAST_DRIFTER_PROFILE` and `PURE_DRIFTER_PROFILE`
      (tests/maritime/test_platform_profile.py)

## 10. Bundled M1 Profiles — Implementation

- [x] 10.1 Three module-level `NodeProfile` constants (`ANCHOR_PROFILE`, `BALLAST_DRIFTER_PROFILE`, `PURE_DRIFTER_PROFILE`) constructed from numbers sourced from `docs/maritime_buoy_design.md` — sensor list, state dim, comms profile, compute budget, and pump/mooring/satellite flags per class; doc citations in module docstring
      (rtl/vectors/maritime/platform_profile.py)

- [x] 10.2 `ALL_M1_PROFILES: tuple[NodeProfile, ...]` aggregating the three profiles in order (anchor, ballast_drifter, pure_drifter)
      (rtl/vectors/maritime/platform_profile.py)

## 11. CapabilityViolation — Tests

- [x] 11.1 Exception is importable, raisable with a message, and subclasses `Exception`
      (tests/maritime/test_platform_profile.py)

## 12. CapabilityViolation — Implementation

- [x] 12.1 `CapabilityViolation(Exception)` defined in `platform_profile.py` with docstring clarifying it is raised by downstream modules, not by this module
      (rtl/vectors/maritime/platform_profile.py)

## 13. Verification

- [x] 13.1 `uv run pytest tests/maritime/test_platform_profile.py` passes with zero failures
- [x] 13.2 Frozen baseline intact — `git diff` shows zero modifications to `experiments/01*.py` through `experiments/11*.py` and existing `rtl/vectors/*.py` files
- [x] 13.3 Module imports cleanly without optional dependencies — `uv run python -c "from rtl.vectors.maritime.platform_profile import ANCHOR_PROFILE, BALLAST_DRIFTER_PROFILE, PURE_DRIFTER_PROFILE, CapabilityViolation"` exits 0
- [x] 13.4 `openspec validate maritime-platform-profile --strict` passes
