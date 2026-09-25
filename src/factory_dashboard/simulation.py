from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from .logic import color_class
from .models import Health, Snapshot, SourceMode


class FactorySimulation:
    """Safe visualization-only simulation derived from the supplied PLC sequence."""

    PHASES = [
        ("HBW retrieve", 3.0),
        ("Crane transfer", 3.0),
        ("Oven door / prepare", 2.0),
        ("Retract oven slider", 2.0),
        ("Burning", 5.0),
        ("Open oven", 2.0),
        ("Extend oven slider", 2.0),
        ("Close oven / settle", 1.0),
        ("Delivery", 4.0),
        ("Sawing", 3.0),
        ("Move to sorting", 2.0),
        ("Sorting / color detection", 2.0),
        ("Sorting / storage", 3.0),
    ]

    COLORS = [(250, "White"), (150, "Red"), (50, "Blue")]

    def __init__(self):
        self.running = True
        self.t0 = datetime.now(timezone.utc)
        self.offset = 0.0
        self._last_cycle = 0

    def reset(self) -> None:
        self.t0 = datetime.now(timezone.utc)
        self.offset = 0.0
        self._last_cycle = 0
        self.running = True

    def toggle(self) -> None:
        if self.running:
            self.offset = (datetime.now(timezone.utc) - self.t0).total_seconds()
        else:
            self.t0 = datetime.now(timezone.utc) - timedelta(seconds=self.offset)
        self.running = not self.running

    @property
    def cycle(self) -> int:
        return self._last_cycle

    def snapshot(self) -> Snapshot:
        now = datetime.now(timezone.utc)
        elapsed = (now - self.t0).total_seconds() if self.running else self.offset
        self.offset = max(0.0, elapsed)
        total = sum(d for _, d in self.PHASES)
        cycle = int(elapsed // total)
        self._last_cycle = cycle
        cycle_time = elapsed % total

        cursor = 0.0
        phase_index = len(self.PHASES) - 1
        phase = self.PHASES[-1][0]
        phase_elapsed = 0.0
        phase_remaining = 0.0
        for i, (name, duration) in enumerate(self.PHASES):
            if cycle_time < cursor + duration:
                phase_index = i
                phase = name
                phase_elapsed = cycle_time - cursor
                phase_remaining = duration - phase_elapsed
                break
            cursor += duration

        color_value, color_name = self.COLORS[cycle % len(self.COLORS)]
        values = self._base_values()
        self._apply_phase(values, phase_index, color_value, color_name)
        values.update({
            "sim.phase": phase,
            "sim.phase_index": phase_index,
            "sim.phase_elapsed_s": phase_elapsed,
            "sim.phase_remaining_s": phase_remaining,
            "sim.cycle": cycle,
            "sim.running": self.running,
            "sl.sensor.color_value": color_value,
            "sim.color": color_name,
            "local.ms_step": self._ms_step_for_phase(phase_index),
            "local.emergency_not_pressed": True,
            "local.emergency_memory": False,
            "local.sl_sorting_requested": phase_index in (11, 12),
            "local.crane_secured": phase_index in (1, 8),
            "local.crane_coord_h": 890 if phase_index >= 2 else 1347,
            "local.crane_coord_v": 870 if phase_index >= 2 else 170,
            "local.crane_coord_r": 500,
            "local.sl_workpiece_coord": 3 if phase_index in (11, 12) else 0,
        })
        return Snapshot(
            timestamp=now,
            values=values,
            statuses={k: "Good" for k in values},
            source=SourceMode.SIMULATION,
            connected=True,
            emergency=False,
            message=f"Simulation: {phase} · {color_name} workpiece",
            health=Health.OK,
            last_good_timestamp=now,
            good_count=len(values),
            bad_count=0,
        )

    @staticmethod
    def _ms_step_for_phase(phase: int) -> int:
        if phase <= 7:
            return 0
        if phase == 8:
            return 1
        return 2

    @staticmethod
    def _base_values() -> dict[str, Any]:
        keys = [
            "ms.motor.turntable_cw", "ms.motor.turntable_ccw", "ms.motor.conveyor",
            "ms.motor.saw", "ms.motor.slider_in", "ms.motor.slider_out",
            "ms.motor.transfer_oven", "ms.motor.transfer_turntable", "ms.process.burn",
            "ms.air.compressor", "ms.valve.vacuum", "ms.valve.transfer", "ms.valve.oven_door",
            "ms.valve.ejector", "hbw.motor.conveyor_forward", "hbw.motor.conveyor_backward",
            "hbw.motor.crane_rack", "hbw.motor.crane_conveyor", "hbw.motor.crane_down",
            "hbw.motor.crane_up", "hbw.motor.cantilever_forward", "hbw.motor.cantilever_backward",
            "c.motor.up", "c.motor.down", "c.motor.backward", "c.motor.forward", "c.motor.cw",
            "c.motor.ccw", "c.air.compressor", "c.valve.vacuum", "pm.motor.conveyor_forward",
            "pm.motor.conveyor_backward", "pm.motor.tool_up", "pm.motor.tool_down",
            "sl.motor.conveyor", "sl.air.compressor", "sl.valve.white", "sl.valve.red", "sl.valve.blue",
            "sl.sensor.white", "sl.sensor.red", "sl.sensor.blue", "ms.sensor.conveyor", "ms.sensor.oven",
            "sl.sensor.before_color", "sl.sensor.after_color",
        ]
        return {key: False for key in keys}

    @staticmethod
    def _apply_phase(values: dict[str, Any], phase: int, color_value: int, color_name: str) -> None:
        if phase == 0:  # HBW retrieval
            values["hbw.motor.crane_conveyor"] = True
            values["hbw.motor.crane_down"] = True
            values["hbw.motor.cantilever_forward"] = True
        elif phase == 1:  # crane transfer to MS
            values["c.air.compressor"] = True
            values["c.motor.forward"] = True
            values["c.motor.up"] = True
            values["c.valve.vacuum"] = True
        elif phase == 2:
            values["ms.air.compressor"] = True
            values["ms.valve.oven_door"] = True
            values["ms.sensor.oven"] = True
        elif phase == 3:
            values["ms.air.compressor"] = True
            values["ms.valve.oven_door"] = True
            values["ms.motor.slider_in"] = True
        elif phase == 4:
            values["ms.process.burn"] = True
            values["ms.sensor.oven"] = True
        elif phase == 5:
            values["ms.air.compressor"] = True
            values["ms.valve.oven_door"] = True
        elif phase == 6:
            values["ms.air.compressor"] = True
            values["ms.valve.oven_door"] = True
            values["ms.motor.slider_out"] = True
        elif phase == 7:
            values["ms.air.compressor"] = True
            values["ms.valve.oven_door"] = True
        elif phase == 8:  # delivery
            values["ms.air.compressor"] = True
            values["ms.motor.transfer_oven"] = True
            values["ms.valve.transfer"] = True
            values["ms.valve.vacuum"] = True
        elif phase == 9:  # sawing
            values["ms.motor.turntable_cw"] = True
            values["ms.motor.saw"] = True
        elif phase == 10:
            values["ms.motor.conveyor"] = True
            values["sl.motor.conveyor"] = True
            values["sl.sensor.before_color"] = True
        elif phase == 11:
            values["ms.motor.conveyor"] = True
            values["sl.motor.conveyor"] = True
            values["sl.sensor.before_color"] = True
            values["sl.sensor.after_color"] = True
        elif phase == 12:
            values["sl.air.compressor"] = True
            if color_name == "White":
                values["sl.valve.white"] = True
            elif color_name == "Red":
                values["sl.valve.red"] = True
            else:
                values["sl.valve.blue"] = True
            values[f"sl.sensor.{color_name.lower()}"] = True
