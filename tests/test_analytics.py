from datetime import datetime, timedelta, timezone

import pandas as pd

from factory_dashboard.analytics import build_cycle_metrics, build_stage_metrics, clean_telemetry


def _df(rows):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return pd.DataFrame([{**{"timestamp": base + timedelta(seconds=i)}, **r} for i, r in enumerate(rows)])


def test_clean_telemetry_normalizes_bool_and_adds_intervals():
    df = _df([
        {"ms.process.burn": "False", "c.motor.up": "False"},
        {"ms.process.burn": "True", "c.motor.up": "True"},
        {"ms.process.burn": "False", "c.motor.up": "False"},
    ])
    out = clean_telemetry(df)
    assert out["ms.process.burn"].dtype == bool
    assert out["sample_interval_s"].iloc[1] == 1
    assert "region.Crane.active" in out.columns


def test_cycle_time_uses_hbw_pickup_to_sorting_entry():
    df = _df([
        {"c.valve.vacuum": False, "hbw.sensor.outside": True, "sl.sensor.before_color": True},
        {"c.valve.vacuum": True, "hbw.sensor.outside": False, "sl.sensor.before_color": True},
        {"c.valve.vacuum": True, "hbw.sensor.outside": True, "sl.sensor.before_color": True},
        {"c.valve.vacuum": False, "hbw.sensor.outside": True, "sl.sensor.before_color": False},
        {"c.valve.vacuum": False, "hbw.sensor.outside": True, "sl.sensor.before_color": True},
    ])
    cycles = build_cycle_metrics(clean_telemetry(df))
    assert len(cycles) == 1
    assert cycles.iloc[0]["cycle_time_s"] == 2
    assert cycles.iloc[0]["start_event"] == "HBW pickup"
    assert cycles.iloc[0]["end_event"] == "Sorting-line entry"


def test_burning_duration_equals_lamp_on_window():
    df = _df([
        {"ms.process.burn": False},
        {"ms.process.burn": True},
        {"ms.process.burn": True},
        {"ms.process.burn": False},
    ])
    stages = build_stage_metrics(clean_telemetry(df))
    burning = stages[(stages["stage"] == "Burning") & (stages["measurement"] == "actuator_on")]
    assert len(burning) == 1
    assert burning.iloc[0]["elapsed_s"] == 2
    assert burning.iloc[0]["actuator_on_s"] == 2


def test_signal_duration_uses_signal_source_timestamps_when_available():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"snapshot_id": 1, "timestamp": base, "ms.process.burn": False, "ms.process.burn.source_ts": base},
        {"snapshot_id": 2, "timestamp": base + timedelta(seconds=1), "ms.process.burn": True, "ms.process.burn.source_ts": base + timedelta(seconds=1.2)},
        {"snapshot_id": 3, "timestamp": base + timedelta(seconds=2), "ms.process.burn": True, "ms.process.burn.source_ts": base + timedelta(seconds=2.3)},
        {"snapshot_id": 4, "timestamp": base + timedelta(seconds=3), "ms.process.burn": False, "ms.process.burn.source_ts": base + timedelta(seconds=3.4)},
    ])
    stages = build_stage_metrics(clean_telemetry(df))
    burning = stages[(stages["stage"] == "Burning") & (stages["measurement"] == "actuator_on")]
    assert len(burning) == 1
    assert burning.iloc[0]["elapsed_s"] == 2.2


def test_time_weighted_activity_uses_analysis_time(monkeypatch):
    from factory_dashboard.visualization import station_activity_figure
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    df = pd.DataFrame({
        "timestamp": [base, base + timedelta(seconds=1), base + timedelta(seconds=9)],
        "analysis_timestamp": [base, base + timedelta(seconds=1), base + timedelta(seconds=9)],
        "region.MS.active": [True, False, False],
    })
    fig = station_activity_figure(df)
    # 1 active second out of 9 total seconds.
    bar = fig.data[0]
    ms_value = float(bar.y[list(bar.x).index("MS")])
    assert round(ms_value, 6) == round(100.0 / 9.0, 6)
