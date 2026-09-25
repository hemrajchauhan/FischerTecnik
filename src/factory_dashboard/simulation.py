from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any

from .logic import color_class
from .models import Health, Snapshot, SourceMode
from .process_model import REFERENCE_CYCLE_SECONDS, REFERENCE_PHASES, phase_from_elapsed


class FactorySimulation:
    """Safe visualization-only simulation based on the measured factory cycle."""

    PHASES = list(REFERENCE_PHASES)
    CYCLE_SECONDS = REFERENCE_CYCLE_SECONDS
    COLORS = [(250, "White"), (150, "Red"), (50, "Blue")]

    def __init__(self):
        self.running = True
        self.t0 = datetime.now(timezone.utc)
        self.offset = 0.0
        self._last_cycle = 1

    def reset(self) -> None:
        self.t0 = datetime.now(timezone.utc)
        self.offset = 0.0
        self._last_cycle = 1
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

        zero_based_cycle = int(elapsed // self.CYCLE_SECONDS)
        self._last_cycle = zero_based_cycle + 1
        cycle_time = elapsed % self.CYCLE_SECONDS
        phase_index, phase, phase_elapsed, phase_remaining = phase_from_elapsed(cycle_time)

        color_value, color_name = self.COLORS[zero_based_cycle % len(self.COLORS)]
        values = self._base_values()
        self._apply_phase(values, phase_index, color_value, color_name)
        # Realistic dummy oven temperature for the cake-factory presentation.
        # This is not a PLC sensor value.
        oven_temperature = 180.0 + 2.2 * math.sin(elapsed / 21.0) + 0.9 * math.sin(elapsed / 6.5)
        values.update({
            "sim.phase": phase,
            "sim.phase_index": phase_index,
            "sim.phase_elapsed_s": phase_elapsed,
            "sim.phase_remaining_s": phase_remaining,
            "sim.cycle": self._last_cycle,
            "sim.cycle_elapsed_s": cycle_time,
            "sim.cycle_period_s": self.CYCLE_SECONDS,
            "sim.running": self.running,
            "sim.oven_temperature_c": oven_temperature,
            "sl.sensor.color_value": color_value,
            "sim.color": color_name,
            # Keep PLC step as a simulated reported value only. It does not gate
            # the station activity model.
            "local.ms_step": self._ms_step_for_phase(phase_index),
            "local.emergency_not_pressed": True,
            "local.emergency_memory": False,
            "local.sl_sorting_requested": phase_index in (6, 7),
            "local.crane_secured": phase_index in (2, 3),
            "local.crane_coord_h": 890 if phase_index >= 2 else 1347,
            "local.crane_coord_v": 870 if phase_index >= 2 else 170,
            "local.crane_coord_r": 500,
            "local.sl_workpiece_coord": 3 if phase_index in (6, 7) else 0,
        })
        return Snapshot(
            timestamp=now,
            values=values,
            statuses={k: "Good" for k in values},
            source=SourceMode.SIMULATION,
            connected=True,
            emergency=False,
            message=f"Simulation: cycle {self._last_cycle} · {phase} · {color_name} workpiece",
            health=Health.OK,
            last_good_timestamp=now,
            good_count=len(values),
            bad_count=0,
        )

    @staticmethod
    def _ms_step_for_phase(phase: int) -> int:
        # Approximation of the PLC's coarse MS step for compatibility only.
        if phase <= 1:
            return 0
        if phase in (2, 3):
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
            "sl.sensor.before_color", "sl.sensor.after_color", "hbw.sensor.outside",
        ]
        values = {key: False for key in keys}
        # Verified active-low light barriers: TRUE = clear, FALSE = workpiece present.
        values["hbw.sensor.outside"] = True
        values["sl.sensor.before_color"] = True
        values["sl.sensor.after_color"] = True
        return values

    @staticmethod
    def _apply_phase(values: dict[str, Any], phase: int, color_value: int, color_name: str) -> None:
        # The cycle is anchored at burning. HBW/C work is deliberately placed in
        # the final phase to demonstrate the real pipeline: the next workpiece
        # can be prepared while the current one is completing sorting.
        if phase == 0:  # Baking + start next cake from ingredient/tray storage
            values["ms.process.burn"] = True
            values["hbw.sensor.outside"] = False
            values["c.valve.vacuum"] = True
            values["ms.sensor.oven"] = True
        elif phase == 1:  # Oven unloading
            values["c.valve.vacuum"] = False
            values["hbw.sensor.outside"] = True
            values["ms.valve.oven_door"] = True
            values["ms.motor.slider_out"] = True
        elif phase == 2:  # Unload from oven
            values["c.valve.vacuum"] = False
            values["hbw.sensor.outside"] = True
            values["ms.motor.transfer_oven"] = True
            values["ms.valve.transfer"] = True
            values["ms.valve.vacuum"] = True
        elif phase == 3:  # Position for finishing
            values["c.valve.vacuum"] = False
            values["hbw.sensor.outside"] = True
            values["ms.motor.transfer_turntable"] = True
            values["ms.valve.transfer"] = True
            values["ms.valve.vacuum"] = True
        elif phase == 4:  # Cake finishing
            values["c.valve.vacuum"] = False
            values["hbw.sensor.outside"] = True
            values["ms.motor.turntable_cw"] = True
            values["ms.motor.saw"] = True
            values["pm.motor.tool_down"] = True
            values["pm.motor.conveyor_forward"] = True
        elif phase == 5:  # Move to quality inspection / piece enters inspection line
            values["c.valve.vacuum"] = False
            values["hbw.sensor.outside"] = True
            values["ms.motor.conveyor"] = True
            values["sl.motor.conveyor"] = True
            # Active-low light barrier: FALSE means the workpiece is present
            # at the sorting-line entry.
            values["sl.sensor.before_color"] = False
        elif phase == 6:  # Colour sorting & dispatch
            values["c.valve.vacuum"] = False
            values["hbw.sensor.outside"] = True
            values["sl.motor.conveyor"] = True
            values["sl.sensor.after_color"] = True
            values["sl.air.compressor"] = True
            if color_name == "White":
                values["sl.valve.white"] = True
            elif color_name == "Red":
                values["sl.valve.red"] = True
            else:
                values["sl.valve.blue"] = True
        elif phase == 7:  # Prepare next cake
            values["c.valve.vacuum"] = False
            values["hbw.sensor.outside"] = False
            values["hbw.motor.crane_conveyor"] = True
            values["hbw.motor.crane_down"] = True
            values["hbw.motor.cantilever_forward"] = True
            values["c.air.compressor"] = True
            values["c.motor.forward"] = True
            values["c.motor.up"] = True
            values["c.valve.vacuum"] = True

        # Avoid unused import warnings and keep the exact PLC color thresholds
        # visible in the simulation data.
        assert color_class(color_value) == color_name
