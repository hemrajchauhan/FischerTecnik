from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from .models import Snapshot


class TelemetryRecorder:
    """Append live telemetry to a timestamped CSV session without overwriting data."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._path: Path | None = None
        self._fieldnames: list[str] = []
        self._last_timestamp: datetime | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    def record(self, snapshot: Snapshot) -> Path:
        with self._lock:
            if self._path is None:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                self._path = self.directory / f"session_{stamp}.csv"

            timestamp = snapshot.timestamp.astimezone(timezone.utc)
            if self._last_timestamp is not None and timestamp <= self._last_timestamp:
                return self._path

            values = dict(snapshot.values)
            if not self._fieldnames:
                self._fieldnames = ["timestamp", *sorted(values)]
                self._write_header()
            else:
                new_fields = sorted(set(values) - set(self._fieldnames))
                if new_fields:
                    self._rewrite_with_fields([*self._fieldnames, *new_fields])

            row = {field: "" for field in self._fieldnames}
            row["timestamp"] = timestamp.isoformat()
            for key, value in values.items():
                row[key] = self._serialize(value)

            with self._path.open("a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=self._fieldnames).writerow(row)
            self._last_timestamp = timestamp
            return self._path

    def close(self) -> None:
        pass

    def _write_header(self) -> None:
        assert self._path is not None
        with self._path.open("w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=self._fieldnames).writeheader()

    def _rewrite_with_fields(self, fields: list[str]) -> None:
        assert self._path is not None
        old_rows: list[dict[str, Any]] = []
        if self._path.exists() and self._path.stat().st_size:
            with self._path.open("r", newline="", encoding="utf-8") as fh:
                old_rows = list(csv.DictReader(fh))
        with self._path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for old in old_rows:
                writer.writerow({field: old.get(field, "") for field in fields})
        self._fieldnames = fields

    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, bool):
            return "True" if value else "False"
        return str(value)
