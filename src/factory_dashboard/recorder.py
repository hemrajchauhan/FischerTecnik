from __future__ import annotations

import csv
import json
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .event_engine import Event
from .analytics import export_analytics
from .models import Snapshot
from .process_model import ProcessState, CycleRecord
from .tags import all_live_nodes


class TelemetryRecorder:
    """Persistent live-session recorder with rolling incident capture."""

    def __init__(self, root: Path, pre_seconds: float = 60.0, post_seconds: float = 60.0):
        self.root = root
        self.pre_seconds = pre_seconds
        self.post_seconds = post_seconds
        self._lock = threading.Lock()
        self.session_dir: Path | None = None
        self._telemetry_file = None
        self._quality_file = None
        self._timestamps_file = None
        self._events_file = None
        self._cycles_file = None
        self._telemetry_writer = None
        self._quality_writer = None
        self._timestamps_writer = None
        self._cycles_writer = None
        self._recorded_cycle_ids: set[int] = set()
        self._fields = list(all_live_nodes().keys())
        self._buffer: deque[Snapshot] = deque()
        self._incidents: dict[str, dict[str, Any]] = {}
        self._last_snapshot: Snapshot | None = None

    @property
    def active(self) -> bool:
        return self.session_dir is not None

    def start(self) -> None:
        if self.active:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self.session_dir = self.root / f"session_{stamp}_{uuid.uuid4().hex[:6]}"
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self._telemetry_file = (self.session_dir / "telemetry.csv").open("w", newline="", encoding="utf-8")
        self._quality_file = (self.session_dir / "telemetry_quality.csv").open("w", newline="", encoding="utf-8")
        self._timestamps_file = (self.session_dir / "telemetry_timestamps.csv").open("w", newline="", encoding="utf-8")
        self._events_file = (self.session_dir / "events.jsonl").open("w", encoding="utf-8")
        self._cycles_file = (self.session_dir / "process_cycles.csv").open("w", newline="", encoding="utf-8")
        fields = ["snapshot_id", "timestamp", "source_timestamp_reference", "sync_spread_ms", *self._fields]
        self._telemetry_writer = csv.DictWriter(self._telemetry_file, fieldnames=fields)
        self._telemetry_writer.writeheader()
        self._quality_writer = csv.DictWriter(self._quality_file, fieldnames=[
            "snapshot_id", "timestamp", "good_count", "bad_count", "source_timestamp_count",
            "server_timestamp_count", "source_min_timestamp", "source_max_timestamp",
            "source_timestamp_reference", "sync_spread_ms", "connected", "health",
        ])
        self._quality_writer.writeheader()
        timestamp_fields = ["snapshot_id", "timestamp", "source_timestamp_reference", "sync_spread_ms"]
        for key in self._fields:
            timestamp_fields.extend([f"{key}.source_ts", f"{key}.server_ts"])
        self._timestamps_writer = csv.DictWriter(self._timestamps_file, fieldnames=timestamp_fields)
        self._timestamps_writer.writeheader()
        self._cycles_writer = csv.DictWriter(
            self._cycles_file,
            fieldnames=["cycle", "start", "end", "duration_s", "start_event", "end_event", "cycle_definition"],
        )
        self._cycles_writer.writeheader()
        self._write_metadata({"started_at": datetime.now(timezone.utc).isoformat(), "registered_tags": len(self._fields)})

    def stop(self) -> None:
        with self._lock:
            if not self.session_dir:
                return
            # Finalize any open incidents with the data captured so far.
            for incident_id, incident in list(self._incidents.items()):
                self._finalize_incident(incident_id, incident)
            # Final analytics pass includes the last incomplete cycle/operation.
            try:
                export_analytics(self.session_dir)
            except Exception:
                pass
            for handle in (self._telemetry_file, self._quality_file, self._timestamps_file, self._events_file, self._cycles_file):
                if handle:
                    handle.flush()
                    handle.close()
            self._telemetry_file = self._quality_file = self._timestamps_file = self._events_file = self._cycles_file = None
            self._telemetry_writer = self._quality_writer = self._timestamps_writer = self._cycles_writer = None
            self._recorded_cycle_ids.clear()
            self._write_metadata({"ended_at": datetime.now(timezone.utc).isoformat()})
            self.session_dir = None
            self._buffer.clear()
            self._incidents.clear()

    def record(self, snapshot: Snapshot) -> None:
        if not self.active:
            self.start()
        with self._lock:
            self._last_snapshot = snapshot
            self._buffer.append(snapshot)
            analytical_now = snapshot.source_timestamp_reference or snapshot.timestamp
            cutoff = analytical_now.timestamp() - self.pre_seconds
            while self._buffer and (self._buffer[0].source_timestamp_reference or self._buffer[0].timestamp).timestamp() < cutoff:
                self._buffer.popleft()
            row = {
                "snapshot_id": snapshot.snapshot_id,
                "timestamp": snapshot.timestamp.isoformat(),
                "source_timestamp_reference": snapshot.source_timestamp_reference.isoformat() if snapshot.source_timestamp_reference else snapshot.timestamp.isoformat(),
            }
            row["sync_spread_ms"] = snapshot.sync_spread_ms if snapshot.sync_spread_ms is not None else ""
            row.update({key: snapshot.values.get(key, "") for key in self._fields})
            self._telemetry_writer.writerow(row)
            source_values = list(snapshot.source_timestamps.values())
            self._quality_writer.writerow({
                "snapshot_id": snapshot.snapshot_id,
                "timestamp": snapshot.timestamp.isoformat(),
                "good_count": snapshot.good_count,
                "bad_count": snapshot.bad_count,
                "source_timestamp_count": len(snapshot.source_timestamps),
                "server_timestamp_count": len(snapshot.server_timestamps),
                "source_min_timestamp": min(source_values).isoformat() if source_values else "",
                "source_max_timestamp": max(source_values).isoformat() if source_values else "",
                "source_timestamp_reference": snapshot.source_timestamp_reference.isoformat() if snapshot.source_timestamp_reference else snapshot.timestamp.isoformat(),
                "sync_spread_ms": snapshot.sync_spread_ms,
                "connected": snapshot.connected,
                "health": snapshot.health.value,
            })
            timestamp_row = {
                "snapshot_id": snapshot.snapshot_id,
                "timestamp": snapshot.timestamp.isoformat(),
                "source_timestamp_reference": snapshot.source_timestamp_reference.isoformat() if snapshot.source_timestamp_reference else snapshot.timestamp.isoformat(),
                "sync_spread_ms": snapshot.sync_spread_ms,
            }
            for key in self._fields:
                timestamp_row[f"{key}.source_ts"] = snapshot.source_timestamps.get(key, "").isoformat() if key in snapshot.source_timestamps else ""
                timestamp_row[f"{key}.server_ts"] = snapshot.server_timestamps.get(key, "").isoformat() if key in snapshot.server_timestamps else ""
            self._timestamps_writer.writerow(timestamp_row)
            self._telemetry_file.flush()
            self._quality_file.flush()
            self._timestamps_file.flush()
            self._update_incidents(snapshot)

    def record_process_state(self, state: ProcessState) -> None:
        """Persist a completed cycle exactly once with measured phase durations."""
        if not self.active or state.completed_cycle is None or self._cycles_writer is None:
            return
        record: CycleRecord = state.completed_cycle
        if record.cycle in self._recorded_cycle_ids:
            return
        row = {
            "cycle": record.cycle,
            "start": record.start.isoformat(),
            "end": record.end.isoformat(),
            "duration_s": record.duration_s,
            "start_event": "HBW pickup",
            "end_event": "Sorting-line entry",
            "cycle_definition": "HBW pickup -> sorting-line entry",
        }
        self._cycles_writer.writerow(row)
        self._cycles_file.flush()
        self._recorded_cycle_ids.add(record.cycle)
        # Refresh derived, plot-ready datasets at each completed cycle. Raw
        # telemetry remains the source of truth; analytics are reproducible.
        try:
            export_analytics(self.session_dir)
        except Exception:
            # Never interrupt live recording because an analytics export failed.
            pass

    def record_event(self, event: Event) -> None:
        if not self.active:
            return
        with self._lock:
            self._events_file.write(json.dumps({
                "timestamp": event.timestamp.isoformat(),
                "category": event.category,
                "severity": event.severity,
                "code": event.code,
                "message": event.message,
                "details": event.details,
                "snapshot_id": event.snapshot_id,
                "recovered": event.recovered,
            }, default=str) + "\n")
            self._events_file.flush()
            if not event.recovered and event.severity in {"CRITICAL", "WARNING"}:
                incident_id = f"incident_{event.timestamp.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"
                self._incidents[incident_id] = {
                    "event": event,
                    "rows": list(self._buffer),
                    "post_until": event.timestamp.timestamp() + self.post_seconds,
                }

    def _update_incidents(self, snapshot: Snapshot) -> None:
        for incident_id, incident in list(self._incidents.items()):
            incident["rows"].append(snapshot)
            snapshot_time = (snapshot.source_timestamp_reference or snapshot.timestamp).timestamp()
            if snapshot_time >= incident["post_until"]:
                self._finalize_incident(incident_id, incident)

    def _finalize_incident(self, incident_id: str, incident: dict[str, Any]) -> None:
        if not self.session_dir:
            return
        # Keep incident clips inside the session so a replay session is self-contained.
        directory = self.session_dir / "incidents" / incident_id
        directory.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        for snap in incident["rows"]:
            row = {
                "snapshot_id": snap.snapshot_id,
                "timestamp": snap.timestamp.isoformat(),
                "source_timestamp_reference": snap.source_timestamp_reference.isoformat() if snap.source_timestamp_reference else snap.timestamp.isoformat(),
                "sync_spread_ms": snap.sync_spread_ms if snap.sync_spread_ms is not None else "",
            }
            row.update({key: snap.values.get(key, "") for key in self._fields})
            rows.append(row)
        if rows:
            with (directory / "incident.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        event: Event = incident["event"]
        (directory / "metadata.json").write_text(json.dumps({
            "incident_id": incident_id,
            "session": self.session_dir.name,
            "event": {
                "timestamp": event.timestamp.isoformat(),
                "category": event.category,
                "severity": event.severity,
                "code": event.code,
                "message": event.message,
                "details": event.details,
            },
            "pre_seconds": self.pre_seconds,
            "post_seconds": self.post_seconds,
        }, indent=2, default=str), encoding="utf-8")
        del self._incidents[incident_id]

    def _write_metadata(self, updates: dict[str, Any]) -> None:
        if not self.session_dir:
            return
        path = self.session_dir / "metadata.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data.update(updates)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
