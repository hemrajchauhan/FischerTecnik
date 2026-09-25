from datetime import datetime, timezone
from pathlib import Path

from factory_dashboard.models import Snapshot, SourceMode, Health
from factory_dashboard.recorder import TelemetryRecorder


def test_quality_csv_accepts_source_timestamp_reference(tmp_path: Path):
    recorder = TelemetryRecorder(tmp_path)
    recorder.start()
    ts = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    snap = Snapshot(
        timestamp=ts,
        values={},
        statuses={},
        source=SourceMode.LIVE,
        connected=True,
        health=Health.OK,
        good_count=1,
        bad_count=0,
        snapshot_id=1,
        source_timestamps={"LocalVariables.iC_CoordH": ts},
        server_timestamps={"LocalVariables.iC_CoordH": ts},
        sync_spread_ms=0.0,
    )
    recorder.record(snap)
    recorder.stop()
    quality = next(tmp_path.glob("session_*/telemetry_quality.csv"))
    text = quality.read_text(encoding="utf-8")
    assert "source_timestamp_reference" in text.splitlines()[0]
    assert ts.isoformat() in text
