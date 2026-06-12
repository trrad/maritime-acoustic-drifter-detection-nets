"""Back-of-envelope power-budget calculator for "small, almost-passive"
drifter nodes. All numbers are order-of-magnitude; the goal is to sense-
check whether the prototype's control cadence + depth range is
plausible within a small primary-cell battery over a 1-week mission.

Component power assumptions are deliberately loose. Tighten them once
hardware selection narrows. Sources/rationale summarised inline.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------
RHO_WATER = 1025.0        # kg/m³ (seawater)
G = 9.81                  # m/s²
PUMP_EFFICIENCY = 0.35    # 30–50% typical for small pumps against hydrostatic P


# ---------------------------------------------------------------------------
# Node-scale assumptions (envelope: "small, almost-passive")
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NodeSpec:
    name: str
    battery_wh: float          # usable capacity (Wh). Primary lithium typical.
    mission_days: float
    # Continuous / near-continuous draws (mW):
    mcu_sleep_mw: float = 0.01
    mcu_active_mw: float = 5.0
    mcu_active_duty: float = 0.01   # fraction of time awake
    # Comms (LoRa-style):
    lora_tx_mw: float = 100.0       # transmit
    lora_tx_duty: float = 0.0005    # 1.8 s/h = 5e-4 (1 packet/min × 0.03s)
    lora_rx_mw: float = 30.0        # receive-listen
    lora_rx_duty: float = 0.02      # ~29 min/day listen window total
    # Sensors:
    imu_mw: float = 3.0             # low-power MEMS IMU continuous
    imu_duty: float = 0.1           # 10% of time sampling
    pressure_mw: float = 0.5
    pressure_duty: float = 0.01
    gps_mw: float = 50.0            # cold/warm start
    gps_sec_per_fix: float = 8.0    # warm fix
    gps_fixes_per_hour: float = 2.0 # every 30 min
    # Optional onboard ADCP (for the PF sensor-fusion extension):
    adcp_mw: float = 150.0
    adcp_duty: float = 0.0          # disabled by default; set > 0 to include
    # Inference / ASIC / PF compute:
    compute_mw: float = 3.0
    compute_duty: float = 0.2       # 20% of time running a PF step + control
    # Buoyancy-engine / pump constants:
    displacement_per_transit_ml: float = 80.0  # water moved to change buoyancy by ΔV
    # (A typical small profiler shifts ~50–150 ml per transit to flip buoyancy sign.)


# ---------------------------------------------------------------------------
# Continuous-power computation
# ---------------------------------------------------------------------------
def continuous_draw_mw(spec: NodeSpec) -> dict[str, float]:
    """Per-component average power draw (mW) assuming steady-state duty cycles."""
    gps_duty = spec.gps_fixes_per_hour * spec.gps_sec_per_fix / 3600.0
    return {
        "mcu_sleep": spec.mcu_sleep_mw * (1 - spec.mcu_active_duty),
        "mcu_active": spec.mcu_active_mw * spec.mcu_active_duty,
        "lora_tx": spec.lora_tx_mw * spec.lora_tx_duty,
        "lora_rx": spec.lora_rx_mw * spec.lora_rx_duty,
        "imu": spec.imu_mw * spec.imu_duty,
        "pressure": spec.pressure_mw * spec.pressure_duty,
        "gps": spec.gps_mw * gps_duty,
        "adcp": spec.adcp_mw * spec.adcp_duty,
        "compute": spec.compute_mw * spec.compute_duty,
    }


def pump_energy_j_per_transit(avg_depth_m: float,
                               spec: NodeSpec) -> float:
    """Work (J) to move `displacement_per_transit_ml` ml of water at the
    hydrostatic pressure equivalent to the average depth during the
    transit, then divide by pump efficiency.

    Simplifying assumption: buoyancy flip uses a fixed displacement
    volume per transit regardless of depth-delta size. In reality a
    small delta needs less volume shift, so this OVER-estimates energy
    for short transits. Conservative for the sense-check.
    """
    p_pa = RHO_WATER * G * avg_depth_m + 101_325  # absolute
    dv_m3 = spec.displacement_per_transit_ml * 1e-6
    work_j = p_pa * dv_m3
    return work_j / PUMP_EFFICIENCY


# ---------------------------------------------------------------------------
# Main budget solver
# ---------------------------------------------------------------------------
def budget_report(spec: NodeSpec,
                   transit_avg_depth_m: float,
                   transits_per_hour: float) -> dict:
    """Given a transit schedule, compute energy usage over the mission."""
    mission_hours = spec.mission_days * 24.0
    battery_j = spec.battery_wh * 3600.0  # Wh → J

    # Continuous draws.
    cont_mw = continuous_draw_mw(spec)
    cont_total_mw = sum(cont_mw.values())
    cont_energy_j = cont_total_mw * 1e-3 * mission_hours * 3600.0

    # Pumping.
    e_per_transit_j = pump_energy_j_per_transit(transit_avg_depth_m, spec)
    total_transits = transits_per_hour * mission_hours
    pump_energy_j = total_transits * e_per_transit_j

    total_j = cont_energy_j + pump_energy_j
    total_wh = total_j / 3600.0
    frac_of_budget = total_j / battery_j

    # Also compute: at this continuous draw, how many transits can the
    # node afford in the mission window? (pump energy budget only.)
    pump_budget_j = battery_j - cont_energy_j
    max_transits = pump_budget_j / e_per_transit_j if e_per_transit_j > 0 else float("inf")
    max_transits_per_hour = max_transits / mission_hours

    return {
        "spec_name": spec.name,
        "mission_days": spec.mission_days,
        "battery_wh": spec.battery_wh,
        "continuous_mw_breakdown": cont_mw,
        "continuous_total_mw": cont_total_mw,
        "continuous_energy_wh": cont_energy_j / 3600.0,
        "pump_j_per_transit": e_per_transit_j,
        "requested_transits_per_hour": transits_per_hour,
        "requested_total_transits": total_transits,
        "pump_energy_wh": pump_energy_j / 3600.0,
        "total_energy_wh": total_wh,
        "frac_of_budget": frac_of_budget,
        "max_transits_in_mission": max_transits,
        "max_transits_per_hour": max_transits_per_hour,
    }


def fmt_mw(v: float) -> str:
    return f"{v:>8.3f} mW"


def print_report(r: dict) -> None:
    print(f"\n{'='*70}")
    print(f"Scenario: {r['spec_name']}   "
          f"battery {r['battery_wh']:.0f} Wh × {r['mission_days']:.0f} days")
    print(f"{'='*70}")
    print("Continuous-draw breakdown (average mW):")
    for k, v in sorted(r["continuous_mw_breakdown"].items(),
                        key=lambda kv: -kv[1]):
        print(f"  {k:<14} {fmt_mw(v)}")
    print(f"  {'TOTAL':<14} {fmt_mw(r['continuous_total_mw'])}")
    print(f"  continuous energy over mission: {r['continuous_energy_wh']:.2f} Wh "
          f"({r['continuous_energy_wh']/r['battery_wh']*100:.0f}% of battery)")

    print()
    print(f"Pump energy: {r['pump_j_per_transit']:.1f} J per transit "
          f"(@ avg depth and displacement from NodeSpec)")
    print(f"  requested schedule: {r['requested_transits_per_hour']:.1f}/hr × "
          f"{r['mission_days']*24:.0f}h = {r['requested_total_transits']:.0f} transits")
    print(f"  pump energy over mission: {r['pump_energy_wh']:.2f} Wh "
          f"({r['pump_energy_wh']/r['battery_wh']*100:.0f}% of battery)")
    print()
    print(f"TOTAL mission energy: {r['total_energy_wh']:.2f} Wh "
          f"({r['frac_of_budget']*100:.0f}% of battery)")
    if r["frac_of_budget"] > 1.0:
        print(f"  ⚠ over budget — need to cut transits, comms duty, or sensing")
    print()
    print(f"With this continuous draw, pump budget allows up to "
          f"{r['max_transits_in_mission']:.0f} transits ≈ "
          f"{r['max_transits_per_hour']:.2f}/hr over the mission.")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def main() -> None:
    # Matchbox-class: single large watch battery / coin cell.
    # CR2450 @ 3V × 620 mAh ≈ 1.9 Wh usable.
    # At this size, displacement volume is also limited — maybe 5 ml buoyancy
    # flip. LoRa Rx duty must also be slashed to survive.
    watchbat = NodeSpec(
        name="WATCHBAT: 1× CR2450 (~1.9 Wh) / 7-day / tiny pump",
        battery_wh=1.9, mission_days=7,
        displacement_per_transit_ml=5.0,
        lora_rx_duty=0.002,        # 0.2% listen — can't afford more
        mcu_active_duty=0.005,
        compute_duty=0.05,         # 5% — sparse control decisions
        imu_duty=0.02,
        gps_fixes_per_hour=1.0,    # every hour instead of every 30 min
    )
    # Matchbox+2AA: 2× AA lithium primary ≈ 9 Wh.
    matchbox = NodeSpec(
        name="MATCHBOX+2AA: 2× AA lithium (~9 Wh) / 7-day / tiny pump",
        battery_wh=9.0, mission_days=7,
        displacement_per_transit_ml=5.0,
        lora_rx_duty=0.005,        # 0.5% listen
        compute_duty=0.1,
        gps_fixes_per_hour=2.0,
    )
    # Same again but with the smallest plausible onboard current sensor
    # (single-point Doppler / acoustic TOF), ~100 mW active at 5% duty.
    matchbox_with_current = NodeSpec(
        name="MATCHBOX+2AA + point current sensor (5% duty)",
        battery_wh=9.0, mission_days=7,
        displacement_per_transit_ml=5.0,
        lora_rx_duty=0.005,
        compute_duty=0.1,
        gps_fixes_per_hour=2.0,
        adcp_mw=100.0, adcp_duty=0.05,
    )
    # Reference: small Argo-shrink (~40 Wh) — what we had before.
    small = NodeSpec(
        name="SMALL: ~40 Wh / 7-day / no ADCP (for reference)",
        battery_wh=40.0, mission_days=7,
    )

    # Simulation-parameter mappings to schedule columns ("transits/hr"):
    #   - Prototype cadence (decision every 30 min with depth change assumed):
    #     up to 2 transits/hr.
    #   - Sawtooth glider with 5-min cadence: up to 12 transits/hr.
    #   - Conservative "shallow yoyo" style: 0.5 transits/hr (every 2h).
    schedules = [
        ("conservative (every 2h)", 12.5, 0.5),
        ("prototype cadence (30 min)", 12.5, 2.0),
        ("aggressive yoyo (5 min)", 12.5, 12.0),
    ]
    # columns: label, avg_depth_m, transits_per_hour

    for spec in (watchbat, matchbox, matchbox_with_current, small):
        print(f"\n\n### {spec.name} ###")
        for label, avg, rate in schedules:
            print(f"\n-- schedule: {label}  (avg_d={avg:.0f}m, "
                  f"{rate}/hr) --")
            r = budget_report(spec, avg, rate)
            # Concise summary per scenario.
            print(f"  continuous = {r['continuous_total_mw']:.1f} mW → "
                  f"{r['continuous_energy_wh']:.1f} Wh/mission "
                  f"({r['continuous_energy_wh']/spec.battery_wh*100:.0f}%)")
            print(f"  pump       = {r['pump_j_per_transit']:.0f} J × "
                  f"{r['requested_total_transits']:.0f} transits = "
                  f"{r['pump_energy_wh']:.1f} Wh "
                  f"({r['pump_energy_wh']/spec.battery_wh*100:.0f}%)")
            print(f"  TOTAL      = {r['total_energy_wh']:.1f} Wh "
                  f"({r['frac_of_budget']*100:.0f}%)   "
                  f"{'OVER BUDGET' if r['frac_of_budget'] > 1 else 'OK'}")
            print(f"  max pump rate @ this continuous draw: "
                  f"{r['max_transits_per_hour']:.1f} transits/hr")

    # Detailed breakdown for the matchbox case at prototype cadence.
    print("\n" + "=" * 70)
    print("DETAILED: MATCHBOX+2AA, 9Wh, 7-day, 2 transits/hr")
    print("=" * 70)
    r = budget_report(matchbox, 12.5, 2.0)
    print_report(r)


if __name__ == "__main__":
    main()
