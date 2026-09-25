from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    opcua_url: str = os.getenv("OPCUA_URL", "opc.tcp://AKOMI2-CX2030:4840")
    opcua_namespace: int = int(os.getenv("OPCUA_NAMESPACE", "4"))
    connect_timeout: float = float(os.getenv("OPCUA_CONNECT_TIMEOUT", "5"))
    poll_interval: float = float(os.getenv("OPCUA_POLL_INTERVAL", "0.25"))
    refresh_ms: int = int(os.getenv("DASHBOARD_REFRESH_MS", "1000"))
    history_seconds: int = int(os.getenv("HISTORY_SECONDS", "300"))
    stale_after: float = float(os.getenv("OPCUA_STALE_AFTER", "3.0"))
    incident_pre_seconds: float = float(os.getenv("INCIDENT_PRE_SECONDS", "60"))
    incident_post_seconds: float = float(os.getenv("INCIDENT_POST_SECONDS", "60"))
    process_watchdog_seconds: float = float(os.getenv("PROCESS_WATCHDOG_SECONDS", "90"))
    opcua_sync_warning_ms: float = float(os.getenv("OPCUA_SYNC_WARNING_MS", "100"))


SETTINGS = Settings()
ASSETS = ROOT / "assets"
DATA = ROOT / "data"
