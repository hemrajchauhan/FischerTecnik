from datetime import datetime, timezone

from factory_dashboard.logic import color_class, process_phase, region_statuses
from factory_dashboard.models import Health, Snapshot, SourceMode


def snap(values, **kwargs):
    return Snapshot(datetime.now(timezone.utc), values, {}, SourceMode.SIMULATION, True, **kwargs)


def test_burning_highlights_ms():
    s = snap({"ms.process.burn": True})
    regions = {r.key: r for r in region_statuses(s)}
    assert regions["ms"].active
    assert not regions["hbw"].active


def test_process_phase_uses_ms_step():
    assert process_phase(snap({"local.ms_step": 2})) == "Sawing / transfer"


def test_color_thresholds_match_plc():
    assert color_class(221) == "White"
    assert color_class(220) == "Red"
    assert color_class(101) == "Red"
    assert color_class(100) == "Blue"
    assert color_class(0) == "Blue"


def test_emergency_marks_all_regions_fault():
    regions = region_statuses(snap({}, emergency=True, health=Health.EMERGENCY))
    assert all(r.fault for r in regions)


def test_stale_is_not_machine_fault():
    regions = region_statuses(snap({}, health=Health.STALE))
    assert not any(r.fault for r in regions)
