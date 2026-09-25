from pathlib import Path
from unittest.mock import patch

from factory_dashboard.data_source import ReplayDataSource
from factory_dashboard.models import SourceMode


def test_replay_uses_recorded_timestamps(tmp_path: Path):
    path = tmp_path / "sample.csv"
    path.write_text(
        "timestamp,ms.process.burn\n"
        "2026-01-01T00:00:00Z,False\n"
        "2026-01-01T00:00:01Z,True\n"
        "2026-01-01T00:00:03Z,False\n"
    )
    replay = ReplayDataSource(path, loop=False)
    with patch("factory_dashboard.data_source.time.monotonic", side_effect=[10.0, 10.5, 11.2, 13.1]):
        replay.started_monotonic = 10.0
        a = replay.snapshot()
        b = replay.snapshot()
        c = replay.snapshot()
        d = replay.snapshot()
    assert a.bool("ms.process.burn") is False
    assert b.bool("ms.process.burn") is False
    assert c.bool("ms.process.burn") is True
    assert d.bool("ms.process.burn") is False
    assert d.source == SourceMode.REPLAY


def test_replay_loops_by_elapsed_time(tmp_path: Path):
    path = tmp_path / "sample.csv"
    path.write_text(
        "timestamp,ms.process.burn\n"
        "2026-01-01T00:00:00Z,False\n"
        "2026-01-01T00:00:01Z,True\n"
    )
    replay = ReplayDataSource(path, loop=True)
    with patch("factory_dashboard.data_source.time.monotonic", side_effect=[0.0, 1.1, 1.1, 1.2]):
        replay.started_monotonic = 0.0
        first = replay.snapshot()
        second = replay.snapshot()
        third = replay.snapshot()
    assert first.bool("ms.process.burn") is False
    assert second.bool("ms.process.burn") is False
    assert third.bool("ms.process.burn") is False
