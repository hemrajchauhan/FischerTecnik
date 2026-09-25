from datetime import datetime, timezone
import sys
import types

# The test only exercises the reader's in-process buffering. Keep it runnable in
# minimal CI environments where asyncua is not installed.
if "asyncua" not in sys.modules:
    fake_asyncua = types.ModuleType("asyncua")
    fake_asyncua.Client = object
    fake_asyncua.ua = types.SimpleNamespace(NodeId=lambda value, namespace: (value, namespace))
    sys.modules["asyncua"] = fake_asyncua

from factory_dashboard.models import Health, Snapshot, SourceMode
from factory_dashboard.opcua_client import OpcUaReader


def _snap(i: int) -> Snapshot:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Snapshot(
        timestamp=now,
        values={"ms.process.burn": bool(i % 2)},
        statuses={"ms.process.burn": "Good"},
        source=SourceMode.LIVE,
        connected=True,
        health=Health.OK,
        snapshot_id=i,
        good_count=1,
    )


def test_reader_keeps_unread_samples_independent_of_ui_reruns():
    reader = OpcUaReader("opc.tcp://example", 4)
    reader._set_snapshot(_snap(1), keep_history=True)
    reader._set_snapshot(_snap(2), keep_history=True)
    reader._set_snapshot(_snap(3), keep_history=True)

    rows = reader.snapshots_since(1)
    assert [r.snapshot_id for r in rows] == [2, 3]
    assert reader.has_live_data is True
