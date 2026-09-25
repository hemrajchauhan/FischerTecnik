from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .models import Health, Snapshot, SourceMode


class DataSource(ABC):
    mode: SourceMode

    @abstractmethod
    def snapshot(self) -> Snapshot:
        raise NotImplementedError

    def close(self) -> None:
        pass


class ReplayDataSource(DataSource):
    """Timestamp-aware replay. Wall-clock elapsed time selects the recorded row."""

    mode = SourceMode.REPLAY

    def __init__(self, path: Path, loop: bool = True, speed: float = 1.0):
        self.path = path
        self.df = pd.read_csv(path)
        if self.df.empty:
            raise ValueError("Replay CSV is empty")
        if "timestamp" not in self.df.columns:
            raise ValueError("Replay CSV must contain a timestamp column")
        self.df["timestamp"] = pd.to_datetime(self.df["timestamp"], utc=True, errors="coerce")
        self.df = self.df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        if self.df.empty:
            raise ValueError("Replay CSV timestamp column contains no valid timestamps")
        self.loop = loop
        self.speed = max(0.01, float(speed))
        self.reset()

    @property
    def finished(self) -> bool:
        return self.index >= len(self.df) - 1 and self._elapsed_replay_seconds() >= self.duration_seconds

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.df["timestamp"].iloc[-1] - self.df["timestamp"].iloc[0]).total_seconds())

    def reset(self) -> None:
        self.index = 0
        self.started_monotonic = time.monotonic()
        self._last_timestamp = None

    def _elapsed_replay_seconds(self) -> float:
        return (time.monotonic() - self.started_monotonic) * self.speed

    def snapshot(self) -> Snapshot:
        elapsed = self._elapsed_replay_seconds()
        if self.duration_seconds > 0 and elapsed > self.duration_seconds:
            if self.loop:
                self.reset()
                elapsed = 0.0
            else:
                elapsed = self.duration_seconds

        target = self.df["timestamp"].iloc[0] + pd.to_timedelta(elapsed, unit="s")
        eligible = self.df.index[self.df["timestamp"] <= target]
        self.index = int(eligible[-1]) if len(eligible) else 0
        row = self.df.iloc[self.index]
        return self._snapshot_from_row(row, finished=(not self.loop and elapsed >= self.duration_seconds))

    def _snapshot_from_row(self, row: pd.Series, finished: bool = False) -> Snapshot:
        values: dict[str, Any] = {}
        for key, value in row.items():
            if key == "timestamp" or pd.isna(value):
                continue
            values[key] = value.item() if hasattr(value, "item") else value
        timestamp = row["timestamp"].to_pydatetime()
        emergency = bool(values.get("local.emergency_memory", False)) or (
            "local.emergency_not_pressed" in values and not bool(values["local.emergency_not_pressed"])
        )
        return Snapshot(
            timestamp=timestamp,
            values=values,
            statuses={k: "Good" for k in values},
            source=self.mode,
            connected=True,
            emergency=emergency,
            message=f"Replay: {self.path.name}" + (" · end of file" if finished else ""),
            health=Health.EMERGENCY if emergency else Health.OK,
            last_good_timestamp=timestamp,
            good_count=len(values),
            bad_count=0,
        )
