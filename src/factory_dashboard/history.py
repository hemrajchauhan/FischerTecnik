from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .models import Snapshot


@dataclass
class History:
    max_rows: int = 600

    def __post_init__(self) -> None:
        self.rows: deque[dict[str, Any]] = deque(maxlen=self.max_rows)

    def add(self, s: Snapshot) -> None:
        row = {
            "timestamp": s.timestamp,
            "source_timestamp_reference": s.source_timestamp_reference or s.timestamp,
            "sync_spread_ms": s.sync_spread_ms,
            **s.values,
        }
        for key, value in s.source_timestamps.items():
            row[f"{key}.source_ts"] = value
        for key, value in s.server_timestamps.items():
            row[f"{key}.server_ts"] = value
        self.rows.append(row)

    def dataframe(self) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame()
        df = pd.DataFrame(list(self.rows))
        if "timestamp" in df:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        if "source_timestamp_reference" in df.columns:
            df["source_timestamp_reference"] = pd.to_datetime(df["source_timestamp_reference"], utc=True, errors="coerce")
        for col in [c for c in df.columns if c.endswith(".source_ts") or c.endswith(".server_ts")]:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        return df
