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
