from datetime import datetime, timedelta, timezone

from factory_dashboard.event_engine import EventEngine
from factory_dashboard.models import Health, Snapshot, SourceMode
from factory_dashboard.process_model import (
    REFERENCE_CYCLE_SECONDS,
    ProcessMonitor,
    expected_process,
    process_deviations,
)


def make(values, timestamp=None, **kwargs):
    return Snapshot(
        timestamp or datetime.now(timezone.utc), values, {}, SourceMode.LIVE, True,
        health=Health.OK, good_count=len(values), **kwargs
    )


def test_reference_is_physical_phase_not_global_plc_step():
    ref = expected_process({"local.ms_step": 0, "ms.process.burn": True})
    assert ref.name == "Burning"
    assert ref.plc_step == 0


def test_sensor_level_alone_is_not_a_deviation():
    assert process_deviations({"ms.sensor.oven": True}) == []


def test_unexpected_sensor_edge_is_a_deviation():
    deviations = process_deviations(
        {"pm.sensor.entry": True},
        sensor_rising_edges=["pm.sensor.entry"],
    )
    assert any(d["kind"] == "unexpected_sensor_transition" for d in deviations)


def test_event_is_edge_triggered_and_recovers():
    engine = EventEngine()
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    good = make({}, t)
    engine.evaluate(good)
    bad = make({"pm.sensor.entry": False}, t + timedelta(seconds=1))
    first = engine.evaluate(bad)
    second = engine.evaluate(bad)
    assert first
    assert not second
    recovered = engine.evaluate(make({"pm.sensor.entry": True}, t + timedelta(seconds=2)))
    assert any(e.recovered for e in recovered)


def test_cycle_clock_uses_hbw_pickup_and_sorting_entry():
    monitor = ProcessMonitor()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    idle = {"c.valve.vacuum": False, "hbw.sensor.outside": True, "sl.sensor.before_color": True, "ms.process.burn": False}
    monitor.update(idle, t0)
    state1 = monitor.update({**idle, "c.valve.vacuum": True, "hbw.sensor.outside": False}, t0 + timedelta(seconds=10))
    assert state1.cycle == 1
    state2 = monitor.update({**idle, "c.valve.vacuum": False, "sl.sensor.before_color": False}, t0 + timedelta(seconds=60))
    assert state2.completed_cycle is not None
    assert state2.completed_cycle.cycle == 1
    assert state2.completed_cycle.duration_s == 50.0


def test_cycle_cadence_uses_pickup_events_not_burn_events():
    monitor = ProcessMonitor()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {"c.valve.vacuum": False, "hbw.sensor.outside": True, "sl.sensor.before_color": True, "ms.process.burn": False}
    monitor.update(base, t0)
    monitor.update({**base, "c.valve.vacuum": True, "hbw.sensor.outside": False}, t0 + timedelta(seconds=10))
    monitor.update({**base, "c.valve.vacuum": False}, t0 + timedelta(seconds=11))
    state = monitor.update({**base, "c.valve.vacuum": True, "hbw.sensor.outside": False}, t0 + timedelta(seconds=60))
    assert state.observed_cycle_period_s == 50.0


def test_reference_cycle_matches_recorded_run():
    assert 40.0 < REFERENCE_CYCLE_SECONDS < 60.0


def test_wrong_actuator_is_detected_against_reference_clock():
    monitor = ProcessMonitor()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {"c.valve.vacuum": False, "hbw.sensor.outside": True, "sl.sensor.before_color": True, "ms.process.burn": False}
    monitor.update(base, t0)
    monitor.update({**base, "c.valve.vacuum": True, "hbw.sensor.outside": False}, t0 + timedelta(seconds=1))
    monitor.update({**base, "c.valve.vacuum": False, "ms.process.burn": True}, t0 + timedelta(seconds=2))
    state = monitor.update({**base, "ms.process.burn": True, "ms.motor.saw": True}, t0 + timedelta(seconds=3))
    deviations = process_deviations({**base, "ms.process.burn": True, "ms.motor.saw": True}, state=state)
    assert any(d["kind"] == "unexpected_actuator_for_phase" and d["tag"] == "ms.motor.saw" for d in deviations)


def test_live_cycle_uses_material_flow_boundaries():
    monitor = ProcessMonitor()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {"c.valve.vacuum": False, "hbw.sensor.outside": True, "sl.sensor.before_color": True, "ms.process.burn": False}
    monitor.update(base, t0)
    monitor.update({**base, "c.valve.vacuum": True, "hbw.sensor.outside": False}, t0 + timedelta(seconds=2))
    monitor.update({**base, "c.valve.vacuum": False}, t0 + timedelta(seconds=3))
    state = monitor.update({**base, "sl.sensor.before_color": False}, t0 + timedelta(seconds=17))
    assert state.completed_cycle is not None
    assert state.completed_cycle.duration_s == 15.0
    assert state.completed_cycle.phase_durations_s == {}


def test_live_node_registry_contains_only_verified_local_nodes():
    from factory_dashboard.tags import all_live_nodes
    nodes = all_live_nodes()
    assert len(nodes) == 83
    assert nodes["local.crane_coord_h"] == "LocalVariables.iC_CoordH"
    assert nodes["local.crane_coord_v"] == "LocalVariables.iC_CoordV"
    assert nodes["local.crane_coord_r"] == "LocalVariables.iC_CoordR"
    assert "local.emergency_memory" not in nodes
    assert "local.ms_step" not in nodes


def test_oven_sensor_is_active_low_and_triggers_on_beam_break():
    from factory_dashboard.tags import sensor_active_edges, sensor_is_active

    assert not sensor_is_active("ms.sensor.oven", True)
    assert sensor_is_active("ms.sensor.oven", False)
    assert sensor_active_edges({"ms.sensor.oven": False}, {"ms.sensor.oven": True}) == ["ms.sensor.oven"]


def test_process_monitor_uses_signal_source_timestamps_for_cycle_markers():
    monitor = ProcessMonitor()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {"c.valve.vacuum": False, "hbw.sensor.outside": True, "sl.sensor.before_color": True, "ms.process.burn": False}
    monitor.update(base, t0, {"c.valve.vacuum": t0, "hbw.sensor.outside": t0, "sl.sensor.before_color": t0})
    pickup_values = {**base, "c.valve.vacuum": True, "hbw.sensor.outside": False}
    state = monitor.update(pickup_values, t0 + timedelta(seconds=10), {"c.valve.vacuum": t0 + timedelta(seconds=9)})
    assert state.cycle_anchor == t0 + timedelta(seconds=9)
    assert state.cycle_elapsed_s == 0.0
    end_values = {**base, "sl.sensor.before_color": False}
    state = monitor.update(end_values, t0 + timedelta(seconds=20), {"sl.sensor.before_color": t0 + timedelta(seconds=19)})
    assert state.completed_cycle is not None
    assert state.completed_cycle.start == t0 + timedelta(seconds=9)
    assert state.completed_cycle.end == t0 + timedelta(seconds=19)


def test_all_light_barriers_are_active_low_from_plc_logic():
    from factory_dashboard.tags import sensor_is_active
    for key in [
        "ms.sensor.oven", "ms.sensor.conveyor", "sl.sensor.before_color", "sl.sensor.after_color",
        "sl.sensor.white", "sl.sensor.red", "sl.sensor.blue", "pm.sensor.entry", "pm.sensor.tool",
        "hbw.sensor.inside", "hbw.sensor.outside",
    ]:
        assert not sensor_is_active(key, True)
        assert sensor_is_active(key, False)


def test_storage_occupancy_is_not_unexpected_during_other_pipeline_phases():
    from factory_dashboard.process_model import ProcessState, process_deviations
    state = ProcessState(cycle=2, phase_index=4, phase="Sawing", cycle_anchor=datetime(2026, 1, 1, tzinfo=timezone.utc))
    deviations = process_deviations(
        {"sl.sensor.red": False},  # active-low: a stored red piece is present
        state=state,
        sensor_active_edges=["sl.sensor.red"],
    )
    assert not any(d["kind"] == "unexpected_sensor_transition" for d in deviations)


def test_bad_process_signal_does_not_create_false_edge():
    monitor = ProcessMonitor()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {"c.valve.vacuum": False, "hbw.sensor.outside": True, "sl.sensor.before_color": True, "ms.process.burn": False}
    monitor.update(base, t0)
    monitor.update({**base, "c.valve.vacuum": True, "hbw.sensor.outside": False, "ms.process.burn": True}, t0 + timedelta(seconds=1), statuses={"c.valve.vacuum": "Good", "hbw.sensor.outside": "Good", "ms.process.burn": "Good"})
    state = monitor.update(
        {**base, "ms.process.burn": False}, t0 + timedelta(seconds=2),
        statuses={"ms.process.burn": "Bad: timeout", "c.valve.vacuum": "Good", "hbw.sensor.outside": "Good", "sl.sensor.before_color": "Good"},
    )
    assert state.phase == "Burning"


def test_source_timestamp_spread_is_diagnostic_not_operator_warning():
    engine = EventEngine(sync_warning_ms=1.0)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snap = make({"c.valve.vacuum": False, "hbw.sensor.outside": True, "sl.sensor.before_color": True}, t0, sync_spread_ms=500.0)
    events = engine.evaluate(snap)
    assert not any(e.code == "SOURCE_TIMESTAMP_SPREAD" for e in events)
