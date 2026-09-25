from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory_dashboard.models import Snapshot, SourceMode
from factory_dashboard.recorder import TelemetryRecorder


def make_snapshot(ts, value):
    return Snapshot(
        timestamp=ts,
        values={"ms.process.burn": value},
        statuses={"ms.process.burn": "Good"},
        source=SourceMode.LIVE,
        connected=True,
        good_count=1,
    )


def test_recorder_creates_session_and_deduplicates(tmp_path: Path):
    recorder = TelemetryRecorder(tmp_path)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    path = recorder.record(make_snapshot(t0, False))
    recorder.record(make_snapshot(t0, False))
    recorder.record(make_snapshot(t0 + timedelta(seconds=1), True))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert "timestamp" in lines[0]
    assert "True" in lines[2]
