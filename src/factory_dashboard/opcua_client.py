from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any

from asyncua import Client, ua

from .models import Health, Snapshot, SourceMode
from .tags import all_live_nodes


class OpcUaReader:
    """Read-only OPC UA telemetry reader with last-good snapshot retention."""

    def __init__(
        self,
        url: str,
        namespace: int,
        interval: float = 0.25,
        timeout: float = 5.0,
        stale_after: float = 3.0,
    ):
        self.url = url
        self.namespace = namespace
        self.interval = interval
        self.timeout = timeout
        self.stale_after = stale_after
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        now = datetime.now(timezone.utc)
        self._snapshot = Snapshot(
            timestamp=now,
            values={},
            statuses={},
            source=SourceMode.LIVE,
            connected=False,
            message="Not connected",
            health=Health.ERROR,
        )
        self._last_good: Snapshot | None = None

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
                timestamp=s.timestamp,
                values=dict(s.values),
                statuses=dict(s.statuses),
                source=s.source,
                connected=s.connected,
                emergency=s.emergency,
                message=s.message,
                health=s.health,
                last_good_timestamp=s.last_good_timestamp,
                good_count=s.good_count,
                bad_count=s.bad_count,
            )

    def _set_snapshot(self, snapshot: Snapshot) -> None:
        with self._lock:
            self._snapshot = snapshot

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
                        timestamp=now,
                        values=dict(last.values),
                        statuses=dict(last.statuses),
                        source=SourceMode.LIVE,
                        connected=False,
                        emergency=last.emergency,
                        message=(
                            f"Live connection lost: {type(exc).__name__}: {exc}. "
                            f"Showing last known telemetry from {last.timestamp.isoformat()}."
                        ),
                        health=Health.STALE if age <= self.stale_after else Health.ERROR,
                        last_good_timestamp=last.timestamp,
                        good_count=last.good_count,
                        bad_count=last.bad_count,
                    ))
                else:
                    self._set_snapshot(Snapshot(
                        timestamp=now,
                        values={},
                        statuses={},
                        source=SourceMode.LIVE,
                        connected=False,
                        emergency=False,
                        message=f"OPC UA unavailable: {type(exc).__name__}: {exc}",
                        health=Health.ERROR,
                    ))
                await asyncio.sleep(min(2.0, max(self.interval, 0.5)))

    async def _connect_and_poll(self) -> None:
        async with Client(url=self.url, timeout=self.timeout) as client:
            ns = self.namespace
            nodes = {
                key: client.get_node(ua.NodeId(node_id, ns))
                for key, node_id in all_live_nodes().items()
            }
            while not self._stop.is_set():
                values: dict[str, Any] = {}
                statuses: dict[str, str] = {}
                good = bad = 0
                async def read_one(key: str, node: Any) -> tuple[str, Any, str, bool]:
                    try:
                        dv = await node.read_data_value()
                        if not dv.StatusCode.is_good():
                            return key, None, str(dv.StatusCode), False
                        return key, dv.Value.Value, "Good", True
                    except Exception as exc:
                        return key, None, f"Bad: {type(exc).__name__}", False

                results = await asyncio.gather(
                    *(read_one(key, node) for key, node in nodes.items())
                )
                for key, value, status, is_good in results:
                    statuses[key] = status
                    if is_good:
                        values[key] = value
                        good += 1
                    else:
                        bad += 1

                now = datetime.now(timezone.utc)
                emergency = self._derive_emergency(values)
                health = Health.EMERGENCY if emergency else (Health.ERROR if good == 0 else Health.OK)
                message = f"Connected to {self.url} · {good} good / {bad} bad"
                snapshot = Snapshot(
                    timestamp=now,
                    values=values,
                    statuses=statuses,
                    source=SourceMode.LIVE,
                    connected=True,
                    emergency=emergency,
                    message=message,
                    health=health,
                    last_good_timestamp=now if good else self._last_good.timestamp if self._last_good else None,
                    good_count=good,
                    bad_count=bad,
                )
                if good:
                    with self._lock:
                        self._last_good = snapshot
                self._set_snapshot(snapshot)
                await asyncio.sleep(self.interval)

    @staticmethod
    def _derive_emergency(values: dict[str, Any]) -> bool:
        if values.get("local.emergency_memory") is True:
            return True
        if "local.emergency_not_pressed" in values:
            return not bool(values["local.emergency_not_pressed"])
        return False
