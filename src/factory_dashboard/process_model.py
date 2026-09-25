from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import statistics
from collections import deque

from .tags import sensor_active_edges

# These phase names are also used by the simulation.  IMPORTANT:
# live/replay mode does not use these durations as a clock.  The live plant is
# clocked from its own signal transitions and the durations are measured from
# the recorded timestamps.
# Simulation/reference timing only. Live and replay measurements are always
# taken from actual signal transitions and source timestamps.
# The final preparation window completes the 55 s production cadence used by
# the learning-factory process model. It is not imposed on live telemetry.
REFERENCE_PHASES: tuple[tuple[str, float], ...] = (
    ("Baking", 5.0),
    ("Oven unloading", 2.0),
    ("Unload from oven", 9.0),
    ("Position for finishing", 11.0),
    ("Cake finishing", 4.0),
    ("Move to quality control & sorting", 6.0),
    ("Colour sorting & dispatch", 7.0),
    ("Prepare next cake", 11.0),
)
REFERENCE_CYCLE_SECONDS = sum(duration for _, duration in REFERENCE_PHASES)

# PLC iMS_Step is useful as an informational value only. The factory is
# pipelined, so it must never be used as a global activity gate.
PLC_STEP_NAMES = {
    0: "Baking / oven sequence",
    1: "Delivery",
    2: "Cake finishing / downstream",
}

PROCESS_OUTPUTS = {
    "hbw": {
        "hbw.motor.conveyor_forward", "hbw.motor.conveyor_backward",
        "hbw.motor.crane_rack", "hbw.motor.crane_conveyor",
        "hbw.motor.crane_down", "hbw.motor.crane_up",
        "hbw.motor.cantilever_forward", "hbw.motor.cantilever_backward",
    },
    "crane": {
        "c.motor.up", "c.motor.down", "c.motor.backward", "c.motor.forward",
        "c.motor.cw", "c.motor.ccw", "c.valve.vacuum",
    },
    "ms": {
        "ms.motor.turntable_cw", "ms.motor.turntable_ccw", "ms.motor.conveyor",
        "ms.motor.saw", "ms.motor.slider_in", "ms.motor.slider_out",
        "ms.motor.transfer_oven", "ms.motor.transfer_turntable", "ms.process.burn",
        # Pneumatic outputs are real process actuators and therefore belong to
        # station activity. Compressors remain utility signals only.
        "ms.valve.vacuum", "ms.valve.transfer", "ms.valve.oven_door", "ms.valve.ejector",
    },
    "pm": {
        "pm.motor.conveyor_forward", "pm.motor.conveyor_backward",
        "pm.motor.tool_up", "pm.motor.tool_down",
    },
    "sl": {
        "sl.motor.conveyor", "sl.valve.white", "sl.valve.red", "sl.valve.blue",
    },
}

PROCESS_SENSORS = {
    "ms.sensor.oven": "oven",
    "ms.sensor.conveyor": "ms_conveyor",
    "sl.sensor.before_color": "sorting_before_color",
    "sl.sensor.after_color": "sorting_after_color",
    "sl.sensor.white": "white_storage",
    "sl.sensor.red": "red_storage",
    "sl.sensor.blue": "blue_storage",
    "pm.sensor.entry": "pm_entry",
    "pm.sensor.tool": "pm_tool",
    "hbw.sensor.inside": "hbw_inside",
    "hbw.sensor.outside": "hbw_outside",
}

SENSOR_ALLOWED_PHASES = {
    "oven": {"Baking", "Oven unloading", "Unload from oven", "Position for finishing", "Cake finishing", "Move to quality control & sorting", "Colour sorting & dispatch", "Prepare next cake"},
    "ms_conveyor": {"Move to quality control & sorting", "Colour sorting & dispatch"},
    "sorting_before_color": {"Move to quality control & sorting", "Colour sorting & dispatch"},
    "sorting_after_color": {"Move to quality control & sorting", "Colour sorting & dispatch"},
    # Storage light barriers report persistent occupancy and are independent
    # of the currently processed workpiece. A stored red/blue/white piece can
    # legitimately remain present while MS/HBW are processing another piece.
    "white_storage": {"Baking", "Oven unloading", "Unload from oven", "Position for finishing", "Cake finishing", "Move to quality control & sorting", "Colour sorting & dispatch", "Prepare next cake"},
    "red_storage": {"Baking", "Oven unloading", "Unload from oven", "Position for finishing", "Cake finishing", "Move to quality control & sorting", "Colour sorting & dispatch", "Prepare next cake"},
    "blue_storage": {"Baking", "Oven unloading", "Unload from oven", "Position for finishing", "Cake finishing", "Move to quality control & sorting", "Colour sorting & dispatch", "Prepare next cake"},
    "pm_entry": set(),
    "pm_tool": set(),
    # HBW is pipelined and may legitimately move during any MS phase.
    "hbw_inside": {"Baking", "Oven unloading", "Unload from oven", "Position for finishing", "Cake finishing", "Move to quality control & sorting", "Colour sorting & dispatch", "Prepare next cake"},
    "hbw_outside": {"Baking", "Oven unloading", "Unload from oven", "Position for finishing", "Cake finishing", "Move to quality control & sorting", "Colour sorting & dispatch", "Prepare next cake"},
}

# Only outputs for which a wrong phase is meaningful are checked. HBW/C are
# intentionally omitted because they run concurrently with the MS process.
ACTUATOR_ALLOWED_PHASES = {
    "ms.process.burn": {"Baking"},
    "ms.motor.slider_in": {"Baking", "Oven unloading", "Colour sorting & dispatch", "Prepare next cake"},
    "ms.motor.slider_out": {"Oven unloading", "Colour sorting & dispatch", "Prepare next cake"},
    "ms.motor.transfer_oven": {"Unload from oven"},
    "ms.motor.transfer_turntable": {"Position for finishing", "Cake finishing"},
    "ms.motor.turntable_cw": {"Position for finishing", "Cake finishing"},
    "ms.motor.turntable_ccw": {"Move to quality control & sorting", "Colour sorting & dispatch"},
    "ms.motor.saw": {"Cake finishing"},
    "ms.motor.conveyor": {"Move to quality control & sorting", "Colour sorting & dispatch"},
    "ms.valve.oven_door": {"Oven unloading", "Colour sorting & dispatch", "Prepare next cake"},
    "ms.valve.transfer": {"Unload from oven", "Position for finishing", "Cake finishing"},
    "ms.valve.vacuum": {"Unload from oven", "Position for finishing", "Cake finishing"},
    "ms.valve.ejector": {"Cake finishing", "Move to quality control & sorting"},
    "sl.motor.conveyor": {"Move to quality control & sorting", "Colour sorting & dispatch"},
    "sl.valve.white": {"Colour sorting & dispatch"},
    "sl.valve.red": {"Colour sorting & dispatch"},
    "sl.valve.blue": {"Colour sorting & dispatch"},
    "pm.motor.conveyor_forward": set(),
    "pm.motor.conveyor_backward": set(),
    "pm.motor.tool_up": set(),
    "pm.motor.tool_down": set(),
}

CONTRADICTION_PAIRS = (
    ("c.motor.up", "c.motor.down", "Crane UP and DOWN active simultaneously"),
    ("c.motor.forward", "c.motor.backward", "Crane FORWARD and BACKWARD active simultaneously"),
    ("c.motor.cw", "c.motor.ccw", "Crane CW and CCW active simultaneously"),
    ("hbw.motor.conveyor_forward", "hbw.motor.conveyor_backward", "HBW conveyor FORWARD and BACKWARD active simultaneously"),
    ("hbw.motor.crane_rack", "hbw.motor.crane_conveyor", "HBW crane commanded toward rack and conveyor simultaneously"),
    ("hbw.motor.crane_up", "hbw.motor.crane_down", "HBW crane UP and DOWN active simultaneously"),
    ("hbw.motor.cantilever_forward", "hbw.motor.cantilever_backward", "HBW cantilever FORWARD and BACKWARD active simultaneously"),
    ("ms.motor.turntable_cw", "ms.motor.turntable_ccw", "MS turntable CW and CCW active simultaneously"),
    ("ms.motor.slider_in", "ms.motor.slider_out", "MS slider IN and OUT active simultaneously"),
    ("pm.motor.conveyor_forward", "pm.motor.conveyor_backward", "PM conveyor FORWARD and BACKWARD active simultaneously"),
)


@dataclass(frozen=True)
class ExpectedProcess:
    step: int | None
    name: str
    allowed_active: frozenset[str]
    source: str = "inferred"
    plc_step: int | None = None


@dataclass(frozen=True)
class CycleRecord:
    cycle: int
    start: datetime
    end: datetime
    duration_s: float
    phase_durations_s: dict[str, float]
    incomplete_phases: tuple[str, ...] = ()


# Production-cycle markers. These describe the material flow, not the MS
# burning sequence. The cycle starts when the vacuum gripper picks a piece
# from the HBW hand-off position and ends when that piece reaches the sorting
# line entry light barrier.
CYCLE_START_VACUUM = "c.valve.vacuum"
CYCLE_START_HBW_SENSOR = "hbw.sensor.outside"
CYCLE_END_SENSOR = "sl.sensor.before_color"


def sensor_is_active_value(key: str, value: Any) -> bool:
    """Semantic sensor state for cycle markers without importing tag helpers here."""
    if value is None:
        return False
    # All verified light barriers are active-low in this PLC.
    return not bool(value)


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def phase_from_elapsed(elapsed_s: float) -> tuple[int, str, float, float]:
    """Simulation-only fixed phase clock."""
    elapsed = max(0.0, elapsed_s) % REFERENCE_CYCLE_SECONDS
    cursor = 0.0
    for index, (name, duration) in enumerate(REFERENCE_PHASES):
        if elapsed < cursor + duration:
            inside = elapsed - cursor
            return index, name, inside, max(0.0, duration - inside)
        cursor += duration
    index, (name, duration) = len(REFERENCE_PHASES) - 1, REFERENCE_PHASES[-1]
    return index, name, duration, 0.0


def infer_process_phase(values: dict[str, Any]) -> ExpectedProcess:
    plc_step = _as_int(values.get("local.ms_step"))
    if bool(values.get("ms.process.burn", False)):
        return ExpectedProcess(0, "Baking", frozenset(PROCESS_OUTPUTS["ms"]), "signal", plc_step)
    if bool(values.get("ms.motor.slider_out", False)) or bool(values.get("ms.valve.oven_door", False)):
        return ExpectedProcess(1, "Oven unloading", frozenset(PROCESS_OUTPUTS["ms"]), "signal", plc_step)
    if bool(values.get("ms.motor.transfer_oven", False)):
        return ExpectedProcess(2, "Unload from oven", frozenset(PROCESS_OUTPUTS["ms"]), "signal", plc_step)
    if bool(values.get("ms.motor.transfer_turntable", False)):
        return ExpectedProcess(3, "Position for finishing", frozenset(PROCESS_OUTPUTS["ms"]), "signal", plc_step)
    if bool(values.get("ms.motor.saw", False)) or bool(values.get("ms.motor.turntable_cw", False)):
        return ExpectedProcess(4, "Cake finishing", frozenset(PROCESS_OUTPUTS["ms"]), "signal", plc_step)
    if bool(values.get("ms.motor.conveyor", False)) or bool(values.get("sl.motor.conveyor", False)):
        return ExpectedProcess(5, "Move to quality control & sorting", frozenset(PROCESS_OUTPUTS["ms"] | PROCESS_OUTPUTS["sl"]), "signal", plc_step)
    if any(bool(values.get(k, False)) for k in ("sl.valve.white", "sl.valve.red", "sl.valve.blue")):
        return ExpectedProcess(6, "Colour sorting & dispatch", frozenset(PROCESS_OUTPUTS["sl"]), "signal", plc_step)
    return ExpectedProcess(None, "Idle / waiting", frozenset(), "inferred", plc_step)


def expected_process(values: dict[str, Any], cycle_elapsed_s: float | None = None) -> ExpectedProcess:
    # Kept for simulation/compatibility. Live/replay uses ProcessMonitor's
    # transition-based phase instead of converting elapsed time into a guessed
    # phase with fixed durations.
    if cycle_elapsed_s is not None:
        step, name, _, _ = phase_from_elapsed(cycle_elapsed_s)
        return ExpectedProcess(step, name, frozenset(), "simulation_clock", _as_int(values.get("local.ms_step")))
    return infer_process_phase(values)


@dataclass(frozen=True)
class ProcessState:
    cycle: int = 0
    cycle_elapsed_s: float | None = None
    cycle_progress: float | None = None
    cycle_period_s: float | None = None
    observed_cycle_period_s: float | None = None
    phase_index: int | None = None
    phase: str = "Idle / waiting"
    phase_elapsed_s: float = 0.0
    phase_remaining_s: float = 0.0
    phase_duration_observed_s: float | None = None
    cycle_anchor: datetime | None = None
    plc_step: int | None = None
    reference_source: str = "inferred"
    phase_durations_s: dict[str, float] | None = None
    completed_cycle: CycleRecord | None = None


class ProcessMonitor:
    """Measure the actual live/replay process instead of imposing a fixed clock.

    The production cycle follows the physical material flow: pickup from HBW
    to placement at the sorting-line entry. The MS burn signal is a process
    phase marker only. HBW pickup and sorting-line placement can overlap across
    different workpieces, so open cycle starts are kept in FIFO order.
    """

    PHASES = (
        "Baking",
        "Oven unloading",
        "Unload from oven",
        "Position for finishing",
        "Cake finishing",
        "Move to quality control & sorting",
        "Colour sorting & dispatch",
        "Prepare next cake",
    )

    def __init__(self) -> None:
        self.state = ProcessState()
        self._previous_values: dict[str, Any] = {}
        self._previous_timestamp: datetime | None = None
        self._cycle_anchor: datetime | None = None
        self._last_burn = False
        self._last_burn_start: datetime | None = None
        self._observed_periods: list[float] = []
        self._open_cycles: deque[tuple[int, datetime]] = deque()
        self._next_cycle_number = 1
        self._last_pickup_start: datetime | None = None
        self._phase_index: int | None = None
        self._phase_started_at: datetime | None = None
        self._phase_durations: dict[str, float] = {}
        self._initialized = False
        self._last_completed_cycle: CycleRecord | None = None
        self._phase_history: dict[str, list[float]] = {}

    def reset(self) -> None:
        self.__init__()

    @staticmethod
    def _rising(values: dict[str, Any], previous: dict[str, Any], tag: str) -> bool:
        return bool(values.get(tag, False)) and not bool(previous.get(tag, False))

    @staticmethod
    def _falling(values: dict[str, Any], previous: dict[str, Any], tag: str) -> bool:
        return not bool(values.get(tag, False)) and bool(previous.get(tag, False))

    def _start_phase(self, index: int, timestamp: datetime) -> None:
        if self._phase_index == index:
            return
        if self._phase_index is not None and self._phase_started_at is not None:
            duration = max(0.0, (timestamp - self._phase_started_at).total_seconds())
            self._phase_durations[self.PHASES[self._phase_index]] = duration
        self._phase_index = index
        self._phase_started_at = timestamp

    def _finish_cycle(self, end: datetime) -> CycleRecord | None:
        """Close the oldest unmatched material-flow cycle at sorting entry."""
        if not self._open_cycles:
            return None
        # Ignore a sorting-line edge that belongs to a piece that entered the
        # observed window before its HBW pickup. Keep the real pickup open.
        if self._open_cycles[0][1] >= end:
            return None
        cycle_number, start = self._open_cycles.popleft()
        duration = (end - start).total_seconds()
        record = CycleRecord(
            cycle=cycle_number,
            start=start,
            end=end,
            duration_s=duration,
            # Phase timing is intentionally not attached to the material cycle:
            # the factory is pipelined, so MS phases may belong to another
            # workpiece while this material is travelling downstream.
            phase_durations_s={},
            incomplete_phases=(),
        )
        self._last_completed_cycle = record
        return record

    def _signal_timestamp(self, signal: str, fallback: datetime, source_timestamps: dict[str, datetime]) -> datetime:
        return source_timestamps.get(signal, fallback)

    def _advance_from_signals(
        self, values: dict[str, Any], timestamp: datetime, source_timestamps: dict[str, datetime]
    ) -> None:
        if self._phase_index is None:
            return
        previous = self._previous_values
        # Each phase boundary is timestamped with the SourceTimestamp of the
        # signal that actually caused the transition. This prevents a slow
        # node read from changing the measured stage duration.
        if self._phase_index == 0 and self._falling(values, previous, "ms.process.burn"):
            self._start_phase(1, self._signal_timestamp("ms.process.burn", timestamp, source_timestamps))
        elif self._phase_index == 1 and self._rising(values, previous, "ms.motor.transfer_oven"):
            self._start_phase(2, self._signal_timestamp("ms.motor.transfer_oven", timestamp, source_timestamps))
        elif self._phase_index == 2 and self._rising(values, previous, "ms.motor.transfer_turntable"):
            self._start_phase(3, self._signal_timestamp("ms.motor.transfer_turntable", timestamp, source_timestamps))
        elif self._phase_index == 3 and self._rising(values, previous, "ms.motor.turntable_cw"):
            self._start_phase(4, self._signal_timestamp("ms.motor.turntable_cw", timestamp, source_timestamps))
        elif self._phase_index == 3 and self._rising(values, previous, "ms.motor.saw"):
            self._start_phase(4, self._signal_timestamp("ms.motor.saw", timestamp, source_timestamps))
        elif self._phase_index == 4 and self._rising(values, previous, "ms.motor.conveyor"):
            self._start_phase(5, self._signal_timestamp("ms.motor.conveyor", timestamp, source_timestamps))
        elif self._phase_index == 5 and self._falling(values, previous, "ms.motor.conveyor"):
            self._start_phase(6, self._signal_timestamp("ms.motor.conveyor", timestamp, source_timestamps))
        elif self._phase_index == 6 and self._rising(values, previous, "ms.valve.oven_door"):
            self._start_phase(7, self._signal_timestamp("ms.valve.oven_door", timestamp, source_timestamps))
        elif self._phase_index == 6 and self._rising(values, previous, "ms.motor.slider_in"):
            self._start_phase(7, self._signal_timestamp("ms.motor.slider_in", timestamp, source_timestamps))

    def update(
        self,
        values: dict[str, Any],
        timestamp: datetime,
        source_timestamps: dict[str, datetime] | None = None,
        statuses: dict[str, str] | None = None,
    ) -> ProcessState:
        source_timestamps = source_timestamps or {}
        statuses = statuses or {}
        analytical_timestamp = max(source_timestamps.values()) if source_timestamps else timestamp
        if self._previous_timestamp is not None and analytical_timestamp < self._previous_timestamp:
            self.reset()

        # A bad/missing OPC UA value is unknown, not FALSE. Hold the last good
        # value for edge/phase inference so a transient read failure cannot
        # manufacture OFF/ON transitions.
        effective_values = dict(values)
        for key, status in statuses.items():
            if status != "Good" and key in self._previous_values:
                effective_values[key] = self._previous_values[key]
            elif status != "Good":
                effective_values.pop(key, None)

        burn = bool(effective_values.get("ms.process.burn", False))
        burn_rising = burn and not self._last_burn
        pickup = (
            bool(effective_values.get(CYCLE_START_VACUUM, False))
            and not bool(self._previous_values.get(CYCLE_START_VACUUM, False))
            and not bool(effective_values.get(CYCLE_START_HBW_SENSOR, True))
        )
        sorting_entry = (
            sensor_is_active_value(CYCLE_END_SENSOR, effective_values.get(CYCLE_END_SENSOR))
            and not sensor_is_active_value(CYCLE_END_SENSOR, self._previous_values.get(CYCLE_END_SENSOR, True))
        )
        self._last_completed_cycle = None

        # A piece can be picked before the previous piece reaches sorting. Keep
        # all open material cycles and close them FIFO when pieces reach the
        # sorting-line entry.
        if pickup:
            pickup_timestamp = self._signal_timestamp(CYCLE_START_VACUUM, analytical_timestamp, source_timestamps)
            cycle_number = self._next_cycle_number
            self._next_cycle_number += 1
            self._open_cycles.append((cycle_number, pickup_timestamp))
            if self._last_pickup_start is not None:
                period = (pickup_timestamp - self._last_pickup_start).total_seconds()
                if 20.0 <= period <= 180.0:
                    self._observed_periods.append(period)
                    self._observed_periods = self._observed_periods[-12:]
            self._last_pickup_start = pickup_timestamp
            self._cycle_anchor = pickup_timestamp

        if sorting_entry:
            end_timestamp = self._signal_timestamp(CYCLE_END_SENSOR, analytical_timestamp, source_timestamps)
            completed = self._finish_cycle(end_timestamp)
            if self._open_cycles:
                self._cycle_anchor = self._open_cycles[-1][1]
            elif completed is not None:
                self._cycle_anchor = None
        else:
            completed = None

        if burn_rising:
            burn_timestamp = self._signal_timestamp("ms.process.burn", analytical_timestamp, source_timestamps)
            self._phase_durations = {}
            self._phase_index = 0
            self._phase_started_at = burn_timestamp

        if pickup or burn_rising:
            current_cycle = self._next_cycle_number - 1
            anchor = self._open_cycles[-1][1] if self._open_cycles else self._cycle_anchor
            if anchor is None:
                anchor = analytical_timestamp
            elapsed = max(0.0, (analytical_timestamp - anchor).total_seconds())
            period = self._observed_periods[-1] if self._observed_periods else None
            phase = self.PHASES[self._phase_index] if self._phase_index is not None else "Awaiting next cycle"
            self.state = ProcessState(
                cycle=current_cycle,
                cycle_elapsed_s=elapsed,
                cycle_progress=None,
                cycle_period_s=period,
                observed_cycle_period_s=period,
                phase_index=self._phase_index,
                phase=phase,
                phase_elapsed_s=0.0,
                phase_remaining_s=0.0,
                phase_duration_observed_s=None,
                cycle_anchor=anchor,
                plc_step=_as_int(effective_values.get("local.ms_step")),
                reference_source="HBW pickup → sorting-line entry",
                phase_durations_s=dict(self._phase_durations),
                completed_cycle=completed,
            )
        elif self._cycle_anchor is not None:
            self._advance_from_signals(effective_values, timestamp, source_timestamps)
            elapsed = max(0.0, (analytical_timestamp - self._cycle_anchor).total_seconds())
            period = self._observed_periods[-1] if self._observed_periods else None
            phase = self.PHASES[self._phase_index] if self._phase_index is not None else "Awaiting next cycle"
            phase_elapsed = (
                max(0.0, (analytical_timestamp - self._phase_started_at).total_seconds())
                if self._phase_started_at is not None else 0.0
            )
            # We do not fabricate a remaining time for a live phase. If a
            # previous cycle contains this phase, show its observed duration as
            # a reference instead.
            previous_phase_duration = None
            if self._phase_index is not None:
                name = self.PHASES[self._phase_index]
                historical = self._phase_history.get(name, [])
                if historical:
                    previous_phase_duration = statistics.median(historical[-8:])
            progress = None
            if period and period > 0:
                progress = min(1.0, elapsed / period)
            self.state = ProcessState(
                cycle=self.state.cycle,
                cycle_elapsed_s=elapsed,
                cycle_progress=progress,
                cycle_period_s=period,
                observed_cycle_period_s=self._observed_periods[-1] if self._observed_periods else None,
                phase_index=self._phase_index,
                phase=phase,
                phase_elapsed_s=phase_elapsed,
                phase_remaining_s=max(0.0, previous_phase_duration - phase_elapsed) if previous_phase_duration is not None else 0.0,
                phase_duration_observed_s=previous_phase_duration,
                cycle_anchor=self._cycle_anchor,
                plc_step=_as_int(effective_values.get("local.ms_step")),
                reference_source="burn-edge + measured-transitions",
                phase_durations_s=dict(self._phase_durations),
                completed_cycle=None,
            )
        else:
            inferred = infer_process_phase(values)
            self.state = ProcessState(
                cycle=0,
                phase_index=inferred.step,
                phase=inferred.name,
                plc_step=inferred.plc_step,
                reference_source="inferred-before-first-cycle",
                completed_cycle=completed,
            )

        # Add completed phase durations to rolling history after finalising a cycle.
        if self._last_completed_cycle is not None:
            for name, duration in self._last_completed_cycle.phase_durations_s.items():
                self._phase_history.setdefault(name, []).append(duration)
                self._phase_history[name] = self._phase_history[name][-12:]
            # Re-emit the new cycle state with the completed record attached.
            self.state = ProcessState(**{**self.state.__dict__, "completed_cycle": self._last_completed_cycle})

        analytical_timestamp = max(source_timestamps.values()) if source_timestamps else timestamp
        self._previous_values.update(effective_values)
        self._previous_timestamp = analytical_timestamp
        self._last_burn = burn
        self._initialized = True
        return self.state

    def active_sensor_edges(self, values: dict[str, Any], statuses: dict[str, str] | None = None) -> list[str]:
        """Return physical sensor activations, respecting polarity and quality."""
        if not self._initialized:
            return []
        statuses = statuses or {}
        usable = {k: v for k, v in values.items() if statuses.get(k, "Good") == "Good"}
        return sensor_active_edges(usable, self._previous_values)

    def rising_edges(self, values: dict[str, Any]) -> list[str]:
        """Backward-compatible raw rising-edge API."""
        if not self._initialized:
            return []
        return [key for key in PROCESS_SENSORS if self._rising(values, self._previous_values, key)]

    @property
    def previous_values(self) -> dict[str, Any]:
        return self._previous_values


def process_deviations(
    values: dict[str, Any],
    *,
    state: ProcessState | None = None,
    sensor_rising_edges: list[str] | None = None,
    sensor_active_edges: list[str] | None = None,
) -> list[dict[str, Any]]:
    deviations: list[dict[str, Any]] = []

    for left, right, message in CONTRADICTION_PAIRS:
        if bool(values.get(left, False)) and bool(values.get(right, False)):
            deviations.append({
                "kind": "contradictory_outputs",
                "tag": left,
                "related_tag": right,
                "expected": False,
                "actual": True,
                "step": state.phase_index if state else None,
                "step_name": state.phase if state else "Unknown",
                "message": message,
            })

    reference_phase = state.phase if state is not None else infer_process_phase(values).name
    reference_phase_is_clocked = state is not None and state.cycle > 0 and state.phase not in {"Idle / waiting", "Awaiting next cycle"}
    for tag, allowed_phases in ACTUATOR_ALLOWED_PHASES.items():
        if reference_phase_is_clocked and bool(values.get(tag, False)) and reference_phase not in allowed_phases:
            deviations.append({
                "kind": "unexpected_actuator_for_phase",
                "tag": tag,
                "expected": sorted(allowed_phases),
                "actual": True,
                "phase": reference_phase,
                "message": f"{tag} is active during {reference_phase}, outside its expected process window.",
            })

    active_edges = sensor_active_edges if sensor_active_edges is not None else sensor_rising_edges
    if active_edges:
        for key in active_edges:
            role = PROCESS_SENSORS.get(key)
            allowed = SENSOR_ALLOWED_PHASES.get(role, set())
            # PM sensors are not part of the observed burning/delivery/sawing
            # route, so a rising edge is suspicious even before the first burn
            # edge has established a cycle. Other sensors are only checked once
            # the live process clock exists.
            if role in {"pm_entry", "pm_tool"} and not reference_phase_is_clocked:
                deviations.append({
                    "kind": "unexpected_sensor_transition",
                    "tag": key,
                    "expected": [],
                    "actual": True,
                    "phase": reference_phase,
                    "message": f"{key} became physically active while the PM is not part of the observed active process.",
                })
            elif reference_phase_is_clocked and allowed and reference_phase not in allowed:
                deviations.append({
                    "kind": "unexpected_sensor_transition",
                    "tag": key,
                    "expected": sorted(allowed),
                    "actual": True,
                    "phase": reference_phase,
                    "message": f"{key} became physically active during {reference_phase}; this sensor transition is outside its expected process window.",
                })
            elif reference_phase_is_clocked and not allowed and role in {"pm_entry", "pm_tool"}:
                deviations.append({
                    "kind": "unexpected_sensor_transition",
                    "tag": key,
                    "expected": [],
                    "actual": True,
                    "phase": reference_phase,
                    "message": f"{key} became physically active during {reference_phase}; PM is not part of the observed active process.",
                })

    plc_step = _as_int(values.get("local.ms_step"))
    if plc_step is not None and plc_step not in PLC_STEP_NAMES:
        deviations.append({
            "kind": "invalid_process_step",
            "tag": "local.ms_step",
            "expected": "0, 1 or 2",
            "actual": plc_step,
            "message": f"Invalid MS process step reported by PLC: {plc_step}.",
        })

    return deviations
