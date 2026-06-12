"""Mesh-slot schedule for surface-as-action MPC.

LoRa comms only happen at the surface, so any fleet coordination is
offline (committed during prior surface contact). Each node holds a
pre-arranged schedule of `(t_start, t_end)` slots; the MPC may elect
to surface only during a slot, with dwell duration ≤ slot remaining.

For v1 the schedule is static and passed in via `SimConfig`; a real
fleet would negotiate slots during deploy or prior surface contacts —
out of scope here. The minimal constraint set is sufficient to test
that the MPC respects the constraint pattern without building fleet-
coordination infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MeshSlotSchedule:
    """A static sequence of (t_start_sec, t_end_sec) surface slots,
    sorted ascending. `slot_at_time` and `next_slot_after` answer the
    queries the MPC needs at planning time:

      - `slot_at_time(t)` — is `t` inside an open slot? Returns the
        slot's (start, end) if so, else None.
      - `next_slot_after(t)` — find the next slot starting strictly
        after `t`, for forward-looking gating.
    """

    slots: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        # Validate sorted, non-overlapping. Better to fail loudly here
        # than to plan against an inconsistent schedule.
        prev_end = float("-inf")
        for start, end in self.slots:
            if not (end > start):
                raise ValueError(
                    f"slot end must be > start, got ({start}, {end})"
                )
            if not (start >= prev_end):
                raise ValueError(
                    f"slots must be non-overlapping and ascending, "
                    f"got start={start} after prev_end={prev_end}"
                )
            prev_end = end

    def slot_at_time(self, t_sec: float) -> tuple[float, float] | None:
        """Return the active slot's (start, end) if `t_sec` falls in
        one, else None. Linear scan (fine for v1's small schedules)."""
        for start, end in self.slots:
            if start <= t_sec < end:
                return (start, end)
            if start > t_sec:
                return None
        return None

    def next_slot_after(self, t_sec: float) -> tuple[float, float] | None:
        """Return the next slot starting strictly after `t_sec`, or
        None if no future slot exists."""
        for start, end in self.slots:
            if start > t_sec:
                return (start, end)
        return None

    @staticmethod
    def fixed_interval(period_h: float, dwell_h: float, mission_h: float,
                        first_offset_h: float = 0.0,
                        ) -> "MeshSlotSchedule":
        """Build a regular schedule mirroring the legacy
        `FixedIntervalPolicy` cadence (default 6h period, 30 min dwell).

        `first_offset_h` shifts the first slot — useful when nodes
        deploy at a known offset to avoid simultaneous surfacing across
        a fleet. The default 0 mirrors single-node behaviour.
        """
        if period_h <= 0 or dwell_h <= 0 or mission_h <= 0:
            raise ValueError(
                f"period_h, dwell_h, mission_h must all be > 0; got "
                f"{period_h}, {dwell_h}, {mission_h}"
            )
        slots: list[tuple[float, float]] = []
        t = first_offset_h * 3600.0
        period_sec = period_h * 3600.0
        dwell_sec = dwell_h * 3600.0
        mission_sec = mission_h * 3600.0
        while t < mission_sec:
            end = min(t + dwell_sec, mission_sec)
            slots.append((t, end))
            t += period_sec
        return MeshSlotSchedule(slots=tuple(slots))
