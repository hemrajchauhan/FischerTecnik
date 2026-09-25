from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Health, Snapshot


@dataclass(frozen=True)
class RegionStatus:
    key: str
    label: str
    active: bool
    fault: bool = False
    detail: str = ""


def any_true(snapshot: Snapshot, names: Iterable[str]) -> bool:
    return any(snapshot.bool(name) for name in names)


def region_statuses(s: Snapshot) -> list[RegionStatus]:
    """Map telemetry to physical factory regions.

    Fault state is deliberately derived from explicit safety/telemetry state,
    not from alarm-text matching. This keeps visualization deterministic.
    """
    global_fault = s.health in {Health.ERROR, Health.STALE} and not s.connected
    emergency = s.emergency

    regions = [
        RegionStatus(
            "hbw", "High Bay Warehouse",
            any_true(s, [
                "hbw.motor.conveyor_forward", "hbw.motor.conveyor_backward",
                "hbw.motor.crane_rack", "hbw.motor.crane_conveyor", "hbw.motor.crane_down",
                "hbw.motor.crane_up", "hbw.motor.cantilever_forward", "hbw.motor.cantilever_backward",
            ]), detail="Stacker crane / conveyor activity"),
        RegionStatus(
            "crane", "Crane",
            any_true(s, [
                "c.motor.up", "c.motor.down", "c.motor.backward", "c.motor.forward",
                "c.motor.cw", "c.motor.ccw", "c.valve.vacuum",
            ]), detail="Crane motion / vacuum"),
        RegionStatus(
            "ms", "Machining Station",
            any_true(s, [
                "ms.motor.turntable_cw", "ms.motor.turntable_ccw", "ms.motor.conveyor",
                "ms.motor.saw", "ms.motor.slider_in", "ms.motor.slider_out",
                "ms.motor.transfer_oven", "ms.motor.transfer_turntable", "ms.process.burn",
                "ms.valve.oven_door", "ms.valve.ejector",
            ]) or any_true(s, ["ms.sensor.oven", "ms.sensor.conveyor"]),
            detail="Oven, turntable, saw and transfer unit"),
        RegionStatus(
            "pm", "Punching Machine",
            any_true(s, [
                "pm.motor.conveyor_forward", "pm.motor.conveyor_backward",
                "pm.motor.tool_up", "pm.motor.tool_down",
            ]), detail="Conveyor / punching tool"),
        RegionStatus(
            "sl", "Sorting Line",
            any_true(s, [
                "sl.motor.conveyor", "sl.valve.white", "sl.valve.red", "sl.valve.blue",
            ]) or any_true(s, ["sl.sensor.before_color", "sl.sensor.after_color"]),
            detail="Color detection / storage"),
    ]

    # A stale/error source is shown as a data-quality problem, not as a
    # machine fault. Emergency is the only global red safety state.
    if emergency:
        return [RegionStatus(r.key, r.label, r.active, True, r.detail) for r in regions]
    if global_fault:
        return regions
    return regions


def process_phase(s: Snapshot) -> str:
    if "sim.phase" in s.values:
        return str(s.values["sim.phase"])

    step = s.get("local.ms_step", None)
    if step == 0:
        if s.bool("ms.process.burn"):
            return "Burning"
        return "Oven / Burning sequence"
    if step == 1:
        return "Delivery"
    if step == 2:
        if s.bool("ms.motor.saw"):
            return "Sawing"
        return "Sawing / transfer"
    if s.bool("sl.motor.conveyor") or s.bool("local.sl_sorting_requested"):
        return "Sorting"
    if s.bool("pm.motor.tool_down") or s.bool("pm.motor.tool_up"):
        return "Punching"
    return "Running"


def emergency_source(s: Snapshot) -> str:
    if "local.emergency_memory" in s.values:
        return "PLC emergency memory"
    if "local.emergency_not_pressed" in s.values:
        return "PLC emergency input"
    return "Emergency signal not exposed"


def color_class(value: float | int | None) -> str:
    """Exact classification used by p_ColorSorting.TcPOU."""
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
    return "Blue"


def fault_summary(s: Snapshot) -> list[str]:
    faults: list[str] = []
    if s.emergency:
        faults.append("Emergency shutdown active")
    if s.bad_count:
        faults.append(f"{s.bad_count} telemetry value(s) unavailable")
    if s.health == Health.STALE:
        faults.append("Live telemetry is stale")
    return faults
