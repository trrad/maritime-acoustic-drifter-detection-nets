"""RBPF prototype for passive drifter localization with a pre-loaded
operational forecast prior + optional in-flight bias learning.

Motivated by:
- Paull et al. 2014 IEEE OE — AUV nav + localization survey
- Claus & Bachmayer 2015 J. Field Robotics — bootstrap PF for glider terrain-aided nav
- Zhang et al. 2024 Ocean Engineering — outlier-robust RBPF marginalizing depth bias
- Montemerlo et al. 2003 FastSLAM 2.0 — RBPF canonical template
- Solin & Särkkä 2019 Stat. Comput. — reduced-rank GP via Hilbert space basis

v1 (this module) is position-only RBPF with a multi-sensor observation
stack. v2 will layer a reduced-rank bias-field state per particle
(FastSLAM-2 style).
"""

from .sensors import CTDSensor, LoRaRangeSensor, RelativeFlowSensor  # noqa: F401
from .rbpf import PositionRBPF  # noqa: F401
from .bias_field import BiasFieldState, GridBiasBasis  # noqa: F401
from .surfacing import (  # noqa: F401
    FixedIntervalPolicy,
    GeometricIntervalPolicy,
    UncertaintyGatedPolicy,
    SurfacingPolicy,
)
from .experiment import (  # noqa: F401
    BiasConfig, Experiment, ExperimentResult, PFConfig, SensorConfig,
    SimConfig, StationConfig, run_one_station,
)
from .rts_smoother import (  # noqa: F401
    SmoothedTrajectory, rts_smooth_trajectory,
)

# Re-export Step 2 types for convenient driver wiring (mesh_slots,
# acoustic_events, and process_noise live at the experiments root).
from acoustic_events import (  # noqa: F401, E402
    EventInfo,
    EventScheduleDetector,
    PoissonEventDetector,
)
from mesh_slots import MeshSlotSchedule  # noqa: F401, E402
from process_noise import ProcessNoiseConfig  # noqa: F401, E402
