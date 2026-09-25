from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any
from collections import deque

from asyncua import Client, ua

from .models import Health, Snapshot, SourceMode
from .tags import TAGS, all_live_nodes


class OpcUaReader:
    """Read-only OPC UA reader preserving source/server timestamps per node."""

    def __init__(self, url: str, namespace: int, interval: float = 0.25, timeout: float = 5.0, stale_after: float = 3.0):
        self.url = url
        self.namespace = namespace
        self.interval = interval
        self.timeout = timeout
        self.stale_after = stale_after
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._sequence = 0
        now = datetime.now(timezone.utc)
        self._snapshot = Snapshot(now, {}, {}, SourceMode.LIVE, False, message="Not connected", health=Health.ERROR)
        self._last_good: Snapshot | None = None
        self._recent_snapshots: deque[Snapshot] = deque(maxlen=20000)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_thread, name="opcua-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def snapshot(self) -> Snapshot:
        with self._lock:
            s = self._snapshot
            return Snapshot(
                timestamp=s.timestamp, values=dict(s.values), statuses=dict(s.statuses), source=s.source,
                connected=s.connected, emergency=s.emergency, message=s.message, health=s.health,
                last_good_timestamp=s.last_good_timestamp, good_count=s.good_count, bad_count=s.bad_count,
                snapshot_id=s.snapshot_id, source_timestamps=dict(s.source_timestamps),
                server_timestamps=dict(s.server_timestamps), sync_spread_ms=s.sync_spread_ms,
            )

    @staticmethod
    def _copy_snapshot(s: Snapshot) -> Snapshot:
        return Snapshot(
            timestamp=s.timestamp, values=dict(s.values), statuses=dict(s.statuses), source=s.source,
            connected=s.connected, emergency=s.emergency, message=s.message, health=s.health,
            last_good_timestamp=s.last_good_timestamp, good_count=s.good_count, bad_count=s.bad_count,
            snapshot_id=s.snapshot_id, source_timestamps=dict(s.source_timestamps),
            server_timestamps=dict(s.server_timestamps), sync_spread_ms=s.sync_spread_ms,
        )

    def _set_snapshot(self, snapshot: Snapshot, keep_history: bool = False) -> None:
        with self._lock:
            self._snapshot = snapshot
            if keep_history and snapshot.snapshot_id is not None:
                self._recent_snapshots.append(self._copy_snapshot(snapshot))

    def snapshots_since(self, snapshot_id: int | None = None) -> list[Snapshot]:
        """Return unread live snapshots in acquisition order.

        The OPC UA worker polls independently of Streamlit reruns. The UI can
        therefore switch views or render slowly without losing short-lived PLC
        states from the recording/event pipeline.
        """
        with self._lock:
            rows = list(self._recent_snapshots)
        if snapshot_id is None:
            return rows
        return [row for row in rows if row.snapshot_id is not None and row.snapshot_id > snapshot_id]

    @property
    def has_live_data(self) -> bool:
        with self._lock:
            return self._last_good is not None or bool(self._recent_snapshots)

    def _run_thread(self) -> None:
        asyncio.run(self._worker())

    async def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                await self._connect_and_poll()
            except Exception as exc:
                now = datetime.now(timezone.utc)
                with self._lock:
                    last = self._last_good
                if last is not None:
                    age = (now - (last.last_good_timestamp or last.timestamp)).total_seconds()
                    self._set_snapshot(Snapshot(
                        timestamp=now, values=dict(last.values), statuses=dict(last.statuses), source=SourceMode.LIVE,
                        connected=False, emergency=last.emergency,
                        message=f"Live connection lost: {type(exc).__name__}: {exc}. Showing last known telemetry from {last.timestamp.isoformat()}.",
                        health=Health.STALE if age <= self.stale_after else Health.ERROR,
                        last_good_timestamp=last.timestamp, good_count=last.good_count, bad_count=last.bad_count,
                        snapshot_id=last.snapshot_id, source_timestamps=dict(last.source_timestamps),
                        server_timestamps=dict(last.server_timestamps), sync_spread_ms=last.sync_spread_ms,
                    ))
                else:
                    self._set_snapshot(Snapshot(now, {}, {}, SourceMode.LIVE, False, message=f"OPC UA unavailable: {type(exc).__name__}: {exc}", health=Health.ERROR))
                await asyncio.sleep(min(2.0, max(self.interval, 0.5)))

    async def _read_one(self, key: str, node: Any) -> tuple[str, Any, str, datetime | None, datetime | None, bool]:
        try:
            dv = await node.read_data_value()
            value = dv.Value.Value
            status = str(dv.StatusCode)
            if not dv.StatusCode.is_good():
                return key, None, status, self._dt(dv.SourceTimestamp), self._dt(dv.ServerTimestamp), key in TAGS
            return key, value, "Good", self._dt(dv.SourceTimestamp), self._dt(dv.ServerTimestamp), key in TAGS
        except Exception as exc:
            return key, None, f"Bad: {type(exc).__name__}: {exc}", None, None, key in TAGS

    @staticmethod
    def _dt(value: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    async def _connect_and_poll(self) -> None:
        async with Client(url=self.url, timeout=self.timeout) as client:
            nodes = {key: client.get_node(ua.NodeId(node_id, self.namespace)) for key, node_id in all_live_nodes().items()}
            while not self._stop.is_set():
                results = await asyncio.gather(*(self._read_one(key, node) for key, node in nodes.items()))
                values: dict[str, Any] = {}
                statuses: dict[str, str] = {}
                source_ts: dict[str, datetime] = {}
                server_ts: dict[str, datetime] = {}
                good = bad = 0
                optional_missing = 0
                for key, value, status, src, srv, required in results:
                    statuses[key] = status
                    if status == "Good":
                        values[key] = value
                        good += 1
                    elif required:
                        bad += 1
                    else:
                        optional_missing += 1
                    if src is not None:
                        source_ts[key] = src
                    if srv is not None:
                        server_ts[key] = srv

                self._sequence += 1
                now = datetime.now(timezone.utc)
                spread_ms = None
                if source_ts:
                    spread_ms = (max(source_ts.values()) - min(source_ts.values())).total_seconds() * 1000.0
                emergency = self._derive_emergency(values)
                health = Health.EMERGENCY if emergency else (Health.ERROR if good == 0 else Health.OK)
                snapshot = Snapshot(
                    timestamp=now, values=values, statuses=statuses, source=SourceMode.LIVE,
                    connected=True, emergency=emergency,
                    message=(f"Connected to {self.url} · {good} good / {bad} required bad / {optional_missing} optional unavailable · source spread {spread_ms:.1f} ms"
                             if spread_ms is not None else
                             f"Connected to {self.url} · {good} good / {bad} required bad / {optional_missing} optional unavailable"),
                    health=health, last_good_timestamp=now if good else self._last_good.timestamp if self._last_good else None,
                    good_count=good, bad_count=bad, snapshot_id=self._sequence,
                    source_timestamps=source_ts, server_timestamps=server_ts, sync_spread_ms=spread_ms,
                )
                if good:
                    with self._lock:
                        self._last_good = snapshot
                self._set_snapshot(snapshot, keep_history=True)
                await asyncio.sleep(self.interval)

    @staticmethod
    def _derive_emergency(values: dict[str, Any]) -> bool:
        if values.get("local.emergency_memory") is True:
            return True
        if "local.emergency_not_pressed" in values:
            return not bool(values["local.emergency_not_pressed"])
        return False
