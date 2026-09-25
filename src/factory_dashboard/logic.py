from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Health, Snapshot
from .process_model import PROCESS_OUTPUTS, infer_process_phase


@dataclass(frozen=True)
class RegionStatus:
    key: str
    label: str
    active: bool
    fault: bool = False
    detail: str = ""
    occupied: bool = False
    state_active: bool = False


def any_true(snapshot: Snapshot, names: Iterable[str]) -> bool:
    return any(snapshot.bool(name) for name in names)


# These are deliberately ONLY physical process outputs. A TRUE sensor,
# reference switch, encoder, valve or compressor does not make the image region
# green because the idle recording proves many such signals are TRUE at rest.
REGION_ACTIVITY = {key: tuple(sorted(values)) for key, values in PROCESS_OUTPUTS.items()}

REGION_STATE_SIGNALS: dict[str, tuple[str, ...]] = {
    "hbw": (
        "hbw.sensor.inside", "hbw.sensor.outside", "hbw.sensor.trail_bottom",
        "hbw.sensor.trail_top", "hbw.ref.horizontal", "hbw.ref.vertical",
        "hbw.ref.cantilever_front", "hbw.ref.cantilever_back",
    ),
    "crane": (
        "c.ref.vertical", "c.ref.horizontal", "c.ref.rotate",
    ),
    "ms": (
        "ms.sensor.oven", "ms.sensor.conveyor", "ms.turntable.transfer",
        "ms.turntable.conveyor", "ms.turntable.saw", "ms.transfer.oven",
        "ms.transfer.turntable", "ms.oven.slider_inside", "ms.oven.slider_outside",
    ),
    "pm": (
        "pm.sensor.entry", "pm.sensor.tool", "pm.ref.top", "pm.ref.bottom",
    ),
    "sl": (
        "sl.sensor.before_color", "sl.sensor.after_color", "sl.sensor.white",
        "sl.sensor.red", "sl.sensor.blue",
    ),
}

REGION_UTILITY_SIGNALS: dict[str, tuple[str, ...]] = {
    "crane": ("c.air.compressor", "c.valve.vacuum"),
    "ms": ("ms.air.compressor", "ms.valve.vacuum", "ms.valve.transfer", "ms.valve.oven_door", "ms.valve.ejector"),
    "sl": ("sl.air.compressor",),
}

REGION_LABELS = {
    "hbw": "Ingredient & Tray Storage",
    "crane": "Cake Handling Crane",
    "ms": "Baking Oven & Processing",
    "pm": "Decoration / Finishing",
    "sl": "Quality Inspection & Dispatch",
}

REGION_DETAILS = {
    "hbw": "Ingredient and tray storage with automated retrieval",
    "crane": "Vacuum gripper for cake/tray handling",
    "ms": "Baking oven, transfer and cake-finishing mechanics",
    "pm": "Decoration and finishing station",
    "sl": "Quality inspection, colour detection and flavour dispatch",
}


def _active_tags(s: Snapshot, region: str) -> list[str]:
    return [name for name in REGION_ACTIVITY[region] if s.bool(name)]


def _state_tags(s: Snapshot, region: str) -> list[str]:
    return [name for name in REGION_STATE_SIGNALS[region] if s.bool(name)]


def _utility_tags(s: Snapshot, region: str) -> list[str]:
    return [name for name in REGION_UTILITY_SIGNALS.get(region, ()) if s.bool(name)]


def region_statuses(s: Snapshot) -> list[RegionStatus]:
    """Map telemetry to the physical factory regions.

    Green highlighting means a physical process output is active. Sensor and
    reference states are shown as diagnostics only. This is intentionally not
    an occupancy detector because the idle capture contains many TRUE sensors.
    """
    regions: list[RegionStatus] = []
    for key in ("hbw", "crane", "ms", "pm", "sl"):
        active_tags = _active_tags(s, key)
        state_tags = _state_tags(s, key)
        utility_tags = _utility_tags(s, key)
        active = bool(active_tags)
        detail = REGION_DETAILS[key]
        if active_tags:
            detail += " · Motion/process: " + ", ".join(active_tags)
        else:
            detail += " · No process output active"
        if utility_tags:
            detail += " · Utility: " + ", ".join(utility_tags)
        if state_tags:
            detail += " · Sensor/reference TRUE: " + ", ".join(state_tags)
        regions.append(RegionStatus(
            key=key,
            label=REGION_LABELS[key],
            active=active,
            fault=s.emergency,
            detail=detail,
            # Kept for compatibility with older callers, but intentionally false:
            # a TRUE sensor is not equivalent to verified workpiece occupancy.
            occupied=False,
            state_active=bool(state_tags),
        ))
    return regions


def process_phase(s: Snapshot) -> str:
    if "sim.phase" in s.values:
        return str(s.get("sim.phase"))
    return infer_process_phase(s.values).name


def emergency_source(s: Snapshot) -> str:
    if "local.emergency_memory" in s.values:
        return "PLC emergency memory"
    if "local.emergency_not_pressed" in s.values:
        return "PLC emergency input"
    return "Emergency signal not exposed"


def color_class(value: float | int | None) -> str:
    """Classify a calibrated reflection value.

    The supplied PLC thresholds are only meaningful for calibrated reflection
    values. The captured factory recordings contain mostly 0/1 values while
    idle, so treating every low value as ``Blue`` creates a false blue state.
    Low/un-calibrated values are therefore reported as ``Unknown`` here.
    Simulation uses calibrated values (50/150/250) and continues to classify
    them as Blue/Red/White.
    """
    if value is None:
        return "Unknown"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "Unknown"
    if v > 220:
        return "White"
    if v > 100:
        return "Red"
    if v >= 40:
        return "Blue"
    return "Unknown"


def sorting_color(snapshot: Snapshot, history_df=None) -> tuple[str, str]:
    """Return the best available sorting-color observation and its source.

    Priority is given to the actual sorting actuator because the three valves
    are the PLC's commanded destination for the detected workpiece. This also
    handles short valve pulses that may have already ended by the time the
    dashboard renders the current snapshot, provided the live/replay history
    contains the pulse. A calibrated color-sensor value is used when available.
    Raw low values such as 0/1 from the captured idle recordings are not
    interpreted as Blue.
    """
    valve_colors = (
        ("sl.valve.white", "White"),
        ("sl.valve.red", "Red"),
        ("sl.valve.blue", "Blue"),
    )
    active = [name for name, color in valve_colors if snapshot.bool(name)]
    if len(active) == 1:
        color = next(color for name, color in valve_colors if name == active[0])
        return color, "sorting valve"
    if len(active) > 1:
        return "Unknown", "conflicting sorting valves"

    if history_df is not None and not history_df.empty:
        for name, color in valve_colors:
            if name in history_df.columns:
                mask = history_df[name].fillna(False).astype(bool)
                if mask.any():
                    return color, "last sorting valve"

    sensor_color = color_class(snapshot.get("sl.sensor.color_value"))
    if sensor_color != "Unknown":
        return sensor_color, "color sensor"
    return "Unknown", "unclassified sensor value"


def fault_summary(s: Snapshot) -> list[str]:
    faults: list[str] = []
    if s.emergency:
        faults.append("Emergency shutdown active")
    if s.bad_count:
        faults.append(f"{s.bad_count} telemetry value(s) unavailable")
    if s.health == Health.STALE:
        faults.append("Live telemetry is stale")
    return faults
