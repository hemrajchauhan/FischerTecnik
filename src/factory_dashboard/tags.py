"""OPC UA tag registry derived from the uploaded TwinCAT GVLs.

The screenshots show string NodeIds in namespace 4, e.g.
ns=4;s=gvl_MS.bLamp_MS.  The dashboard therefore keeps the GVL name and
variable name separately and constructs that NodeId at runtime.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Tag:
    key: str
    gvl: str
    node: str
    description: str
    group: str
    kind: str = "bool"

    @property
    def node_id(self) -> str:
        return f"{self.gvl}.{self.node}"


# Only variables with useful process/visualisation meaning are exposed here.
# The full GVLs remain the source of truth for the PLC project.
TAGS: dict[str, Tag] = {}


def _add(gvl: str, group: str, entries: list[tuple[str, str, str]], kind: str = "bool") -> None:
    for node, key, description in entries:
        TAGS[key] = Tag(key, gvl, node, description, group, kind)


_add("gvl_MS", "MS", [
    ("bReferenceSwitch_MS_Turntable_attransferunit", "ms.turntable.transfer", "Turntable at transfer unit"),
    ("bReferenceSwitch_MS_Turntable_atconveyorbelt", "ms.turntable.conveyor", "Turntable at conveyor belt"),
    ("bLightBarrier_MS_conveyorbelt", "ms.sensor.conveyor", "Workpiece on MS conveyor"),
    ("bReferenceSwitch_MS_Turntable_atsaw", "ms.turntable.saw", "Turntable at saw"),
    ("bReferenceSwitch_MS_TransferUnit_atturntable", "ms.transfer.turntable", "Transfer unit at turntable"),
    ("bReferenceSwitch_MS_OvenSlider_inside", "ms.oven.slider_inside", "Oven slider inside"),
    ("bReferenceSwitch_MS_OvenSlider_outside", "ms.oven.slider_outside", "Oven slider outside"),
    ("bReferenceSwitch_MS_TransferUnit_atoven", "ms.transfer.oven", "Transfer unit at oven"),
    ("bLightBarrier_MS_oven", "ms.sensor.oven", "Workpiece at oven"),
    ("bMotor_MS_Turntable_clockwise", "ms.motor.turntable_cw", "Turntable clockwise"),
    ("bMotor_MS_Turntable_counterclockwise", "ms.motor.turntable_ccw", "Turntable counterclockwise"),
    ("bMotor_MS_ConveyorBelt_forward", "ms.motor.conveyor", "MS conveyor forward"),
    ("bMotor_MS_Saw", "ms.motor.saw", "Saw active"),
    ("bMotor_MS_OvenSlider_movein", "ms.motor.slider_in", "Oven slider in"),
    ("bMotor_MS_OvenSlider_moveout", "ms.motor.slider_out", "Oven slider out"),
    ("bMotor_MS_TransferUnit_tooven", "ms.motor.transfer_oven", "Transfer unit to oven"),
    ("bMotor_MS_TransferUnit_toturntable", "ms.motor.transfer_turntable", "Transfer unit to turntable"),
    ("bLamp_MS", "ms.process.burn", "Burning lamp active"),
    ("bCompressor_MS", "ms.air.compressor", "MS compressor"),
    ("bValve_MS_Vacuum", "ms.valve.vacuum", "Vacuum valve"),
    ("bValve_MS_TransferUnit", "ms.valve.transfer", "Transfer-unit cylinder"),
    ("bValve_MS_OvenDoor", "ms.valve.oven_door", "Oven door valve"),
    ("bValve_MS_Ejector", "ms.valve.ejector", "Ejector valve"),
])

_add("gvl_HBW", "HBW", [
    ("bReferenceSwitch_HBW_horizontal", "hbw.ref.horizontal", "Stacker crane horizontal reference"),
    ("bLightBarrier_HBW_inside", "hbw.sensor.inside", "HBW inner light barrier"),
    ("bLightBarrier_HBW_outside", "hbw.sensor.outside", "HBW outer light barrier"),
    ("bReferenceSwitch_HBW_vertical", "hbw.ref.vertical", "Stacker crane vertical reference"),
    ("bReferenceSwitch_HBW_Cantilever_front", "hbw.ref.cantilever_front", "Cantilever extended"),
    ("bReferenceSwitch_HBW_Cantilever_back", "hbw.ref.cantilever_back", "Cantilever retracted"),
    ("bTrailSensor_HBW_bottom", "hbw.sensor.trail_bottom", "HBW bottom trail sensor"),
    ("bTrailSensor_HBW_top", "hbw.sensor.trail_top", "HBW top trail sensor"),
    ("bMotor_HBW_ConveyorBelt_forward", "hbw.motor.conveyor_forward", "HBW conveyor forward"),
    ("bMotor_HBW_ConveyorBelt_backward", "hbw.motor.conveyor_backward", "HBW conveyor backward"),
    ("bMotor_HBW_StackerCrane_torack", "hbw.motor.crane_rack", "Crane toward rack"),
    ("bMotor_HBW_StackerCrane_toconveyorbelt", "hbw.motor.crane_conveyor", "Crane toward conveyor"),
    ("bMotor_HBW_StackerCrane_downward", "hbw.motor.crane_down", "Crane downward"),
    ("bMotor_HBW_StackerCrane_upward", "hbw.motor.crane_up", "Crane upward"),
    ("bMotor_HBW_Cantilever_forward", "hbw.motor.cantilever_forward", "Cantilever forward"),
    ("bMotor_HBW_Cantilever_backward", "hbw.motor.cantilever_backward", "Cantilever backward"),
    ("bEncoderImpulse_HBW_horizontal1", "hbw.encoder.horizontal1", "HBW horizontal encoder impulse A"),
    ("bEncoderImpulse_HBW_horizontal2", "hbw.encoder.horizontal2", "HBW horizontal encoder impulse B"),
    ("bEncoderImpulse_HBW_vertical1", "hbw.encoder.vertical1", "HBW vertical encoder impulse A"),
    ("bEncoderImpulse_HBW_vertical2", "hbw.encoder.vertical2", "HBW vertical encoder impulse B"),
])

_add("gvl_C", "C", [
    ("bReferenceSwitch_C_vertical", "c.ref.vertical", "Crane vertical reference"),
    ("bReferenceSwitch_C_horizontal", "c.ref.horizontal", "Crane horizontal reference"),
    ("bReferenceSwitch_C_rotate", "c.ref.rotate", "Crane rotation reference"),
    ("bMotor_C_upward", "c.motor.up", "Crane upward"),
    ("bMotor_C_downward", "c.motor.down", "Crane downward"),
    ("bMotor_C_backward", "c.motor.backward", "Crane backward"),
    ("bMotor_C_forward", "c.motor.forward", "Crane forward"),
    ("bMotor_C_clockwise", "c.motor.cw", "Crane clockwise"),
    ("bMotor_C_counterclockwise", "c.motor.ccw", "Crane counterclockwise"),
    ("bCompressor_C", "c.air.compressor", "Crane compressor"),
    ("bValve_C", "c.valve.vacuum", "Crane vacuum valve"),
    ("bEncoderImpulse_C_vertical1", "c.encoder.vertical1", "Crane vertical encoder impulse A"),
    ("bEncoderImpulse_C_vertical2", "c.encoder.vertical2", "Crane vertical encoder impulse B"),
    ("bEncoderImpulse_C_horizontal1", "c.encoder.horizontal1", "Crane horizontal encoder impulse A"),
    ("bEncoderImpulse_C_horizontal2", "c.encoder.horizontal2", "Crane horizontal encoder impulse B"),
    ("bEncoderImpulse_C_rotate1", "c.encoder.rotate1", "Crane rotation encoder impulse A"),
    ("bEncoderImpulse_C_rotate2", "c.encoder.rotate2", "Crane rotation encoder impulse B"),
])

_add("gvl_PM", "PM", [
    ("bLightBarrier_PM_entry", "pm.sensor.entry", "Punching-machine entry"),
    ("bLightBarrier_PM_tool", "pm.sensor.tool", "Punching tool position"),
    ("bReferenceSwitch_PM_top", "pm.ref.top", "Punching tool top"),
    ("bReferenceSwitch_PM_bottom", "pm.ref.bottom", "Punching tool bottom"),
    ("bMotor_PM_ConveyorBelt_forward", "pm.motor.conveyor_forward", "PM conveyor forward"),
    ("bMotor_PM_ConveyorBelt_backward", "pm.motor.conveyor_backward", "PM conveyor backward"),
    ("bMotor_PM_Tool_upward", "pm.motor.tool_up", "Punching tool upward"),
    ("bMotor_PM_Tool_downward", "pm.motor.tool_down", "Punching tool downward"),
])

_add("gvl_SL", "SL", [
    ("bPulseCounter_SL", "sl.sensor.pulse", "Sorting-line pulse counter"),
    ("bLightBarrier_SL_beforecolor", "sl.sensor.before_color", "Before color sensor"),
    ("bLightBarrier_SL_aftercolor", "sl.sensor.after_color", "After color sensor"),
    ("bLightBarrier_SL_white", "sl.sensor.white", "White storage occupied"),
    ("bLightBarrier_SL_red", "sl.sensor.red", "Red storage occupied"),
    ("bLightBarrier_SL_blue", "sl.sensor.blue", "Blue storage occupied"),
    ("bMotor_SL_ConveyorBelt", "sl.motor.conveyor", "Sorting conveyor"),
    ("bCompressor_SL", "sl.air.compressor", "Sorting-line compressor"),
    ("bValve_SL_white", "sl.valve.white", "White cylinder"),
    ("bValve_SL_red", "sl.valve.red", "Red cylinder"),
    ("bValve_SL_blue", "sl.valve.blue", "Blue cylinder"),
    ("iColorSensor_SL", "sl.sensor.color_value", "Color sensor reflection value",),
], kind="bool")
# Correct the one integer tag explicitly.
TAGS["sl.sensor.color_value"] = Tag("sl.sensor.color_value", "gvl_SL", "iColorSensor_SL", "Color sensor reflection value", "SL", "int")


# LocalVariables are not part of the stable OPC-UA data contract in this
# project.  The live server capture showed exactly three LocalVariables nodes
# that are valid and useful for visualisation: the crane H/V/R coordinates.
# The other 14 LocalVariables previously registered by the dashboard return
# BadNodeIdUnknown and must NOT be polled every cycle.
VERIFIED_LOCAL_NODES = {
    "local.crane_coord_h": "LocalVariables.iC_CoordH",
    "local.crane_coord_v": "LocalVariables.iC_CoordV",
    "local.crane_coord_r": "LocalVariables.iC_CoordR",
}

# Sensor semantics are defined from the observed idle/run recordings. Most
# light barriers are active when their raw PLC value is TRUE. The MS oven
# light barrier is active-low: TRUE means the beam is clear, FALSE means a
# workpiece is detected. Keep this separate from raw telemetry so the original
# PLC value is never overwritten.
# All light barriers in this PLC use the same physical convention observed
# in the program logic: TRUE = beam clear, FALSE = workpiece present.
# The PLC calls these values directly in conditions such as NOT LightBarrier
# when waiting for a workpiece. Keep the raw value unchanged in telemetry and
# normalize only for process/event interpretation.
SENSOR_ACTIVE_WHEN: dict[str, bool] = {
    key: False for key in (
        "ms.sensor.oven", "ms.sensor.conveyor",
        "sl.sensor.before_color", "sl.sensor.after_color",
        "sl.sensor.white", "sl.sensor.red", "sl.sensor.blue",
        "pm.sensor.entry", "pm.sensor.tool",
        "hbw.sensor.inside", "hbw.sensor.outside",
    )
}

SENSOR_KEYS = tuple(SENSOR_ACTIVE_WHEN.keys())


def sensor_is_active(key: str, value: object) -> bool:
    """Convert a raw sensor value into its physical/semantic active state."""
    raw = bool(value)
    return raw == SENSOR_ACTIVE_WHEN.get(key, True)


def sensor_active_edges(values: dict[str, object], previous: dict[str, object]) -> list[str]:
    """Return sensors whose physical active state just became TRUE."""
    return [
        key for key in SENSOR_KEYS
        if key in values
        and sensor_is_active(key, values[key])
        and not sensor_is_active(key, previous.get(key, not SENSOR_ACTIVE_WHEN.get(key, True)))
    ]


def all_live_nodes() -> dict[str, str]:
    nodes = {key: tag.node_id for key, tag in TAGS.items()}
    nodes.update(VERIFIED_LOCAL_NODES)
    return nodes
