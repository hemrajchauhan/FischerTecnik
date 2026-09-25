from __future__ import annotations

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
    mode = SourceMode.REPLAY

    def __init__(self, path: Path, loop: bool = True):
        self.path = path
        self.df = pd.read_csv(path)
        # Replay the exact source/server timestamp sidecar produced by the
        # recorder. This keeps replay analytics on the same physical timeline
        # as the original live session.
        self._timestamps = None
        timestamp_path = path.parent / "telemetry_timestamps.csv"
        if timestamp_path.exists() and "snapshot_id" in self.df.columns:
            ts = pd.read_csv(timestamp_path)
            if "snapshot_id" in ts.columns:
                self._timestamps = ts.drop_duplicates("snapshot_id", keep="last").set_index("snapshot_id")
        if self.df.empty:
            raise ValueError("Replay CSV is empty")
        if "timestamp" not in self.df.columns:
            raise ValueError("Replay CSV must contain a timestamp column")
        self.df["timestamp"] = pd.to_datetime(self.df["timestamp"], utc=True, errors="coerce")
        if self.df["timestamp"].isna().all():
            raise ValueError("Replay CSV timestamp column contains no valid timestamps")
        self.df = self.df.dropna(subset=["timestamp"]).reset_index(drop=True)
        self.loop = loop
        self.index = 0
        self.started_at = datetime.now(timezone.utc)
        self._last_emit_index = -1

    @property
    def finished(self) -> bool:
        return self.index >= len(self.df)

    def reset(self) -> None:
        self.index = 0
        self.started_at = datetime.now(timezone.utc)
        self._last_emit_index = -1

    @property
    def position(self) -> int:
        return self.index

    @property
    def total_rows(self) -> int:
        return len(self.df)

    @property
    def current_timestamp(self) -> datetime | None:
        if self.df.empty:
            return None
        idx = min(max(self.index - 1, 0), len(self.df) - 1)
        return self.df.iloc[idx]["timestamp"].to_pydatetime()

    @property
    def start_timestamp(self) -> datetime | None:
        return self.df.iloc[0]["timestamp"].to_pydatetime() if not self.df.empty else None

    @property
    def end_timestamp(self) -> datetime | None:
        return self.df.iloc[-1]["timestamp"].to_pydatetime() if not self.df.empty else None

    def seek_index(self, index: int) -> None:
        self.index = min(max(int(index), 0), len(self.df) - 1 if len(self.df) else 0)
        self._last_emit_index = self.index - 1
        self.started_at = datetime.now(timezone.utc)

    def seek_timestamp(self, timestamp: datetime, *, after: bool = False) -> None:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        target = pd.Timestamp(timestamp).tz_convert("UTC")
        values = self.df["timestamp"]
        if after:
            matches = values[values >= target]
        else:
            matches = values[values <= target]
        if matches.empty:
            self.seek_index(0 if after else len(self.df) - 1)
            return
        idx = int(matches.index[-1] if not after else matches.index[0])
        self.seek_index(idx)

    def advance(self, steps: int = 1) -> None:
        if not self.df.empty:
            self.index = min(len(self.df), self.index + max(1, int(steps)))
            self._last_emit_index = self.index - 1

    def snapshots_batch(self, steps: int = 1) -> list[Snapshot]:
        """Return every replayed snapshot in the requested playback batch.

        Playback speed must never skip PLC states. A 10x replay therefore
        processes all intermediate recorded rows and only changes how quickly
        the UI advances through them.
        """
        count = max(1, int(steps))
        rows: list[Snapshot] = []
        for _ in range(count):
            rows.append(self.snapshot())
        return rows

    def snapshot(self) -> Snapshot:
        if self.finished:
            if self.loop:
                self.reset()
            else:
                row = self.df.iloc[-1]
                return self._snapshot_from_row(row, finished=True)

        row = self.df.iloc[self.index]
        self.index += 1
        self._last_emit_index = self.index - 1
        return self._snapshot_from_row(row)

    def _snapshot_from_row(self, row: pd.Series, finished: bool = False) -> Snapshot:
        values: dict[str, Any] = {}
        for key, value in row.items():
            if key in {"snapshot_id", "timestamp", "source_timestamp_reference", "sync_spread_ms"} or key.endswith(".source_ts") or key.endswith(".server_ts"):
                continue
            if pd.isna(value):
                continue
            if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                value = value.strip().lower() == "true"
            values[key] = value.item() if hasattr(value, "item") else value
        timestamp = row["timestamp"].to_pydatetime()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        source_timestamps: dict[str, datetime] = {}
        server_timestamps: dict[str, datetime] = {}
        source_ref = None
        spread = None
        if self._timestamps is not None and "snapshot_id" in row.index and not pd.isna(row.get("snapshot_id")):
            try:
                tsrow = self._timestamps.loc[int(row["snapshot_id"])]
                for col, value in tsrow.items():
                    if pd.isna(value):
                        continue
                    if col.endswith(".source_ts"):
                        parsed = pd.to_datetime(value, utc=True, errors="coerce")
                        if not pd.isna(parsed):
                            source_timestamps[col[:-10]] = parsed.to_pydatetime()
                    elif col.endswith(".server_ts"):
                        parsed = pd.to_datetime(value, utc=True, errors="coerce")
                        if not pd.isna(parsed):
                            server_timestamps[col[:-10]] = parsed.to_pydatetime()
                    elif col == "source_timestamp_reference":
                        parsed = pd.to_datetime(value, utc=True, errors="coerce")
                        if not pd.isna(parsed):
                            source_ref = parsed.to_pydatetime()
                    elif col == "sync_spread_ms":
                        spread = float(value) if str(value).strip() else None
            except (KeyError, ValueError, TypeError):
                pass
        emergency = bool(values.get("local.emergency_memory", False)) or (
            "local.emergency_not_pressed" in values and not bool(values["local.emergency_not_pressed"])
        )
        return Snapshot(
            timestamp=timestamp, values=values, statuses={k: "Good" for k in values},
            source=self.mode, connected=True, emergency=emergency,
            message=f"Replay: {self.path.name}" + (" · end of file" if finished else ""),
            health=Health.EMERGENCY if emergency else Health.OK,
            last_good_timestamp=source_ref or timestamp, good_count=len(values), bad_count=0,
            snapshot_id=int(row["snapshot_id"]) if "snapshot_id" in row.index and not pd.isna(row["snapshot_id"]) else None,
            source_timestamps=source_timestamps, server_timestamps=server_timestamps, sync_spread_ms=spread,
        )
