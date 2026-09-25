from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .models import Snapshot
from .tags import sensor_is_active
from .process_model import ProcessMonitor, ProcessState, process_deviations


@dataclass(frozen=True)
class Event:
    timestamp: datetime
    category: str
    severity: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    snapshot_id: int | None = None
    recovered: bool = False


class EventEngine:
    """Stateful safety/process event detector.

    Events are edge-triggered. The process monitor uses the actual burn rising
    edge as the live/replay cycle anchor, while sensor violations are based on
    sensor transitions rather than raw TRUE levels.
    """

    def __init__(self, watchdog_seconds: float = 90.0, sync_warning_ms: float = 100.0):
        self.watchdog_seconds = watchdog_seconds
        self.sync_warning_ms = sync_warning_ms
        self._active: dict[str, Event] = {}
        self.process_monitor = ProcessMonitor()
        self._last_evaluated_timestamp: datetime | None = None
        self._step_started: tuple[int, datetime] | None = None
        self._sensor_violations: set[str] = set()
        self._last_cycle_anchor: datetime | None = None

    @property
    def process_state(self) -> ProcessState:
        return self.process_monitor.state

    def reset(self) -> None:
        self._active.clear()
        self.process_monitor.reset()
        self._last_evaluated_timestamp = None
        self._step_started = None
        self._sensor_violations.clear()
        self._last_cycle_anchor = None

    def evaluate(self, snapshot: Snapshot) -> list[Event]:
        now = snapshot.source_timestamp_reference or snapshot.timestamp
        # Sensor edges must be calculated against the previous snapshot BEFORE
        # ProcessMonitor updates its previous-value cache. Bad values are not
        # interpreted as FALSE.
        sensor_edges = self.process_monitor.active_sensor_edges(snapshot.values, snapshot.statuses)
        state = self.process_monitor.update(
            snapshot.values, now, source_timestamps=snapshot.source_timestamps, statuses=snapshot.statuses
        )
        candidates: dict[str, Event] = {}

        if snapshot.emergency:
            candidates["emergency"] = self._event(
                snapshot, "SAFETY", "CRITICAL", "EMERGENCY", "Emergency shutdown active."
            )
        if snapshot.source.value == "Live OPC UA" and not snapshot.connected:
            candidates["opcua_disconnect"] = self._event(
                snapshot, "CONNECTION", "CRITICAL", "OPCUA_DISCONNECTED",
                "OPC UA connection is not available.",
            )
        if snapshot.health.value == "stale":
            candidates["stale"] = self._event(
                snapshot, "TELEMETRY", "WARNING", "TELEMETRY_STALE", "Live telemetry is stale."
            )
        if snapshot.bad_count:
            candidates["bad_values"] = self._event(
                snapshot, "TELEMETRY", "WARNING", "BAD_VALUES",
                f"{snapshot.bad_count} telemetry value(s) could not be read.",
            )
        # SourceTimestamp spread is retained as a diagnostic field in the
        # recording, but it is not an operator warning. A multi-node OPC UA
        # snapshot naturally contains slightly different source timestamps;
        # only actual connection/data-quality/process faults should alarm the
        # operator.

        deviations = process_deviations(
            snapshot.values, state=state, sensor_rising_edges=sensor_edges
        )
        # Keep an unexpected sensor event active until that sensor returns FALSE.
        # This gives the alarm edge semantics we want: one START, continuous
        # visibility while the bad condition remains, then one RECOVERED event.
        for tag in list(self._sensor_violations):
            if tag in snapshot.values and snapshot.statuses.get(tag, "Good") == "Good" and not sensor_is_active(tag, snapshot.values[tag]):
                self._sensor_violations.remove(tag)
        for deviation in deviations:
            if deviation["kind"] == "unexpected_sensor_transition":
                self._sensor_violations.add(deviation.get("tag", ""))
        for tag in sorted(self._sensor_violations):
            if tag in snapshot.values and snapshot.statuses.get(tag, "Good") == "Good" and sensor_is_active(tag, snapshot.values[tag]) and not any(
                d.get("tag") == tag and d["kind"] == "unexpected_sensor_transition" for d in deviations
            ):
                deviations.append({
                    "kind": "unexpected_sensor_transition",
                    "tag": tag,
                    "expected": [],
                    "actual": True,
                    "phase": state.phase,
                    "message": f"{tag} remains physically active during {state.phase}; the unexpected sensor condition is still active.",
                })

        for deviation in deviations:
            tag = deviation.get("tag", "")
            related = deviation.get("related_tag", "")
            code = f"PROCESS_{deviation['kind'].upper()}:{tag}:{related}"
            severity = "CRITICAL" if deviation["kind"] in {
                "contradictory_outputs", "unexpected_actuator_for_phase", "invalid_process_step"
            } else "WARNING"
            event_timestamp = snapshot.timestamp_for(tag) if tag else (snapshot.source_timestamp_reference or snapshot.timestamp)
            candidates[code] = Event(
                timestamp=event_timestamp,
                category="PROCESS_LOGIC",
                severity=severity,
                code=code,
                message=deviation["message"],
                details=deviation,
                snapshot_id=snapshot.snapshot_id,
            )

        # Cycle watchdog: use the measured HBW-pickup cadence. This detects a
        # real missing/delayed pickup without imposing a fixed PLC duration.
        if state.cycle > 0 and state.cycle_anchor is not None:
            if self._last_cycle_anchor is None or state.cycle_anchor != self._last_cycle_anchor:
                self._last_cycle_anchor = state.cycle_anchor
            observed = state.observed_cycle_period_s
            if observed is not None and self.watchdog_seconds > 0:
                allowed = observed + 10.0
                elapsed = max(0.0, (now - state.cycle_anchor).total_seconds())
                if elapsed > allowed:
                    candidates["cycle_overdue"] = self._event(
                        snapshot, "PROCESS_WATCHDOG", "WARNING", "CYCLE_OVERDUE",
                        f"No new HBW pickup has started within the expected window ({allowed:.1f}s).",
                        {"elapsed_s": elapsed, "observed_pickup_period_s": observed, "allowed_s": allowed},
                    )

        # Phase watchdog is intentionally generous and source-timestamp based.
        if state.phase_index is not None and self.watchdog_seconds > 0:
            if self._step_started is None or self._step_started[0] != state.phase_index:
                self._step_started = (state.phase_index, now)
            elif (now - self._step_started[1]).total_seconds() > self.watchdog_seconds:
                key = f"watchdog:{state.phase_index}"
                candidates[key] = self._event(
                    snapshot, "PROCESS_WATCHDOG", "WARNING", "PROCESS_PHASE_TIMEOUT",
                    f"Process phase '{state.phase}' has remained active for more than {self.watchdog_seconds:.0f}s.",
                    {"phase_index": state.phase_index, "phase": state.phase,
                     "elapsed_s": (now - self._step_started[1]).total_seconds()},
                )

        events: list[Event] = []
        for key, event in candidates.items():
            if key not in self._active:
                self._active[key] = event
                events.append(event)

        for key, previous in list(self._active.items()):
            if key not in candidates:
                events.append(Event(
                    timestamp=now,
                    category=previous.category,
                    severity="INFO",
                    code=previous.code,
                    message=f"Recovered: {previous.message}",
                    details=previous.details,
                    snapshot_id=snapshot.snapshot_id,
                    recovered=True,
                ))
                del self._active[key]

        self._last_evaluated_timestamp = now
        return events

    @staticmethod
    def _event(
        snapshot: Snapshot,
        category: str,
        severity: str,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> Event:
        return Event(
            timestamp=snapshot.source_timestamp_reference or snapshot.timestamp,
            category=category,
            severity=severity,
            code=code,
            message=message,
            details=details or {},
            snapshot_id=snapshot.snapshot_id,
        )
