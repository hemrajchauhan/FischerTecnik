from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SourceMode(str, Enum):
    LIVE = "Live OPC UA"
    SIMULATION = "Simulation"
    REPLAY = "Replay CSV"


class Health(str, Enum):
    OK = "ok"
    STALE = "stale"
    ERROR = "error"
    EMERGENCY = "emergency"


@dataclass
class Snapshot:
    timestamp: datetime
    values: dict[str, Any]
    statuses: dict[str, str]
    source: SourceMode
    connected: bool
    emergency: bool = False
    message: str = ""
    health: Health = Health.OK
    last_good_timestamp: datetime | None = None
    good_count: int = 0
    bad_count: int = 0
    snapshot_id: int | None = None
    source_timestamps: dict[str, datetime] = field(default_factory=dict)
    server_timestamps: dict[str, datetime] = field(default_factory=dict)
    sync_spread_ms: float | None = None

    def get(self, name: str, default: Any = False) -> Any:
        return self.values.get(name, default)

    def bool(self, name: str) -> bool:
        return bool(self.get(name, False))

    def numeric(self, name: str, default: float = 0.0) -> float:
        value = self.get(name, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @property
    def source_timestamp_reference(self) -> datetime | None:
        """Common analytical timestamp for this snapshot.

        The latest available OPC UA SourceTimestamp is used so the reference
        cannot precede any value included in the snapshot. Individual node
        timestamps remain available in ``source_timestamps`` and the spread
        exposes how far the values are from being source-synchronous.
        """
        if not self.source_timestamps:
            return None
        return max(self.source_timestamps.values())

    def timestamp_for(self, key: str) -> datetime:
        """Return the node SourceTimestamp when available, otherwise snapshot time."""
        return self.source_timestamps.get(key, self.timestamp)

    @property
    def age_s(self) -> float:
        reference = self.last_good_timestamp or self.timestamp
        return max(0.0, (datetime.now(timezone.utc) - reference).total_seconds())

    @property
    def is_stale(self) -> bool:
        return self.health == Health.STALE


@dataclass
class TagValue:
    name: str
    value: Any = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "Good"

    @property
    def age_s(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.timestamp).total_seconds())
