from datetime import datetime, timezone

from factory_dashboard.logic import region_statuses
from factory_dashboard.models import Snapshot, SourceMode


def snapshot(values):
    now = datetime.now(timezone.utc)
    return Snapshot(now, values, {k: "Good" for k in values}, SourceMode.REPLAY, True)


def states(values):
    return {r.key: (r.active, r.occupied) for r in region_statuses(snapshot(values))}


def test_station_sensors_do_not_make_region_active():
    result = states({
        "ms.sensor.oven": True,
        "pm.sensor.entry": True,
        "pm.sensor.tool": True,
        "sl.sensor.before_color": True,
        "sl.sensor.red": True,
        "hbw.sensor.inside": True,
    })
    assert all(active is False for active, _ in result.values())
    assert all(occupied is False for _, occupied in result.values())


def test_actuator_activity_highlights_the_correct_station():
    result = states({
        "pm.motor.tool_down": True,
        "sl.motor.conveyor": True,
        "ms.motor.saw": True,
        "hbw.motor.crane_conveyor": True,
        "c.motor.forward": True,
    })
    assert result["pm"][0] is True
    assert result["sl"][0] is True
    assert result["ms"][0] is True
    assert result["hbw"][0] is True
    assert result["crane"][0] is True


def test_process_actuators_make_station_active_but_compressors_do_not():
    result = states({
        "c.air.compressor": True,
        "c.valve.vacuum": True,
        "ms.air.compressor": True,
        "ms.valve.oven_door": True,
        "sl.air.compressor": True,
    })
    assert result["crane"][0] is True
    assert result["ms"][0] is True
    assert result["hbw"][0] is False
    assert result["pm"][0] is False
    assert result["sl"][0] is False
