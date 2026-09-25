from datetime import datetime, timezone

from factory_dashboard.logic import color_class, process_phase, region_statuses, sorting_color
from factory_dashboard.models import Health, Snapshot, SourceMode


def snap(values, **kwargs):
    return Snapshot(datetime.now(timezone.utc), values, {}, SourceMode.SIMULATION, True, **kwargs)


def test_burning_highlights_ms():
    s = snap({"ms.process.burn": True})
    regions = {r.key: r for r in region_statuses(s)}
    assert regions["ms"].active
    assert not regions["hbw"].active


def test_process_phase_uses_physical_signal():
    assert process_phase(snap({"ms.motor.saw": True})) == "Cake finishing"


def test_color_thresholds_match_plc():
    assert color_class(221) == "White"
    assert color_class(220) == "Red"
    assert color_class(101) == "Red"
    assert color_class(100) == "Blue"
    assert color_class(40) == "Blue"
    assert color_class(1) == "Unknown"
    assert color_class(0) == "Unknown"


def test_emergency_marks_all_regions_fault():
    regions = region_statuses(snap({}, emergency=True, health=Health.EMERGENCY))
    assert all(r.fault for r in regions)


def test_idle_sensor_levels_do_not_make_regions_active_or_occupied():
    regions = {r.key: r for r in region_statuses(snap({
        "ms.sensor.oven": True,
        "pm.sensor.entry": True,
        "pm.sensor.tool": True,
        "sl.sensor.before_color": True,
        "sl.sensor.red": True,
        "hbw.sensor.inside": True,
    }))}
    assert all(not r.active for r in regions.values())
    assert all(not r.occupied for r in regions.values())
    assert all(r.state_active for r in regions.values() if r.key in {"ms", "pm", "sl", "hbw"})


def test_stale_is_not_machine_fault():
    regions = region_statuses(snap({}, health=Health.STALE))
    assert not any(r.fault for r in regions)


def test_sorting_color_uses_active_valve():
    s = snap({"sl.sensor.color_value": 1, "sl.valve.red": True})
    assert sorting_color(s) == ("Red", "sorting valve")


def test_sorting_color_uses_history_for_short_valve_pulse():
    import pandas as pd
    s = snap({"sl.sensor.color_value": 1})
    history = pd.DataFrame([{"sl.valve.white": True, "sl.valve.red": False, "sl.valve.blue": False}])
    assert sorting_color(s, history) == ("White", "last sorting valve")


def test_low_idle_sensor_value_is_not_reported_as_blue():
    s = snap({"sl.sensor.color_value": 1})
    assert sorting_color(s)[0] == "Unknown"
