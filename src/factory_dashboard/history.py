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
        row = {"timestamp": s.timestamp, **s.values}
        self.rows.append(row)

    def dataframe(self) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame()
        df = pd.DataFrame(list(self.rows))
        if "timestamp" in df:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df
