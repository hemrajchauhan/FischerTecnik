from pathlib import Path

from factory_dashboard.data_source import ReplayDataSource
from factory_dashboard.models import SourceMode


def test_replay_advances_rows_and_loops(tmp_path: Path):
    path = tmp_path / "sample.csv"
    path.write_text(
        "timestamp,ms.process.burn\n"
        "2026-01-01T00:00:00Z,False\n"
        "2026-01-01T00:00:01Z,True\n"
    )
    replay = ReplayDataSource(path)
    a = replay.snapshot()
    b = replay.snapshot()
    c = replay.snapshot()
    assert a.source == SourceMode.REPLAY
    assert a.bool("ms.process.burn") is False
    assert b.bool("ms.process.burn") is True
    assert c.bool("ms.process.burn") is False


def test_replay_restores_source_timestamps(tmp_path: Path):
    path = tmp_path / "telemetry.csv"
    path.write_text(
        "snapshot_id,timestamp,ms.process.burn\n"
        "1,2026-01-01T00:00:00Z,False\n"
        "2,2026-01-01T00:00:01Z,True\n"
    )
    (tmp_path / "telemetry_timestamps.csv").write_text(
        "snapshot_id,timestamp,source_timestamp_reference,sync_spread_ms,ms.process.burn.source_ts,ms.process.burn.server_ts\n"
        "1,2026-01-01T00:00:00Z,2026-01-01T00:00:00.100Z,2,2026-01-01T00:00:00.100Z,2026-01-01T00:00:00.110Z\n"
        "2,2026-01-01T00:00:01Z,2026-01-01T00:00:01.250Z,2,2026-01-01T00:00:01.250Z,2026-01-01T00:00:01.260Z\n"
    )
    replay = ReplayDataSource(path)
    replay.snapshot()
    s = replay.snapshot()
    assert s.source_timestamps["ms.process.burn"].isoformat().startswith("2026-01-01T00:00:01.250")
    assert s.sync_spread_ms == 2.0


def test_replay_can_seek_to_timestamp(tmp_path: Path):
    path = tmp_path / "telemetry.csv"
    path.write_text(
        "snapshot_id,timestamp,ms.process.burn\n"
        "1,2026-01-01T00:00:00Z,False\n"
        "2,2026-01-01T00:00:10Z,True\n"
        "3,2026-01-01T00:00:20Z,False\n"
    )
    replay = ReplayDataSource(path)
    replay.seek_timestamp(__import__("datetime").datetime(2026, 1, 1, 0, 0, 10, tzinfo=__import__("datetime").timezone.utc), after=True)
    assert replay.snapshot().snapshot_id == 2


def test_replay_can_advance_multiple_rows(tmp_path: Path):
    path = tmp_path / "telemetry.csv"
    path.write_text(
        "snapshot_id,timestamp,ms.process.burn\n"
        "1,2026-01-01T00:00:00Z,False\n"
        "2,2026-01-01T00:00:01Z,True\n"
        "3,2026-01-01T00:00:02Z,False\n"
    )
    replay = ReplayDataSource(path)
    replay.advance(2)
    assert replay.snapshot().snapshot_id == 3


def test_replay_batch_does_not_skip_recorded_states(tmp_path: Path):
    path = tmp_path / "telemetry.csv"
    path.write_text(
        "snapshot_id,timestamp,ms.process.burn\n"
        "1,2026-01-01T00:00:00Z,False\n"
        "2,2026-01-01T00:00:01Z,True\n"
        "3,2026-01-01T00:00:02Z,False\n"
        "4,2026-01-01T00:00:03Z,True\n"
    )
    replay = ReplayDataSource(path)
    batch = replay.snapshots_batch(3)
    assert [s.snapshot_id for s in batch] == [1, 2, 3]
    assert [s.bool("ms.process.burn") for s in batch] == [False, True, False]
