from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .process_model import PROCESS_OUTPUTS
from .tags import sensor_is_active


BOOL_TRUE = {"true", "1", "1.0", "yes", "on"}
BOOL_FALSE = {"false", "0", "0.0", "no", "off"}

REGION_SIGNAL_MAP = {
    "HBW": tuple(sorted(PROCESS_OUTPUTS["hbw"])),
    "Crane": tuple(sorted(PROCESS_OUTPUTS["crane"])),
    "MS": tuple(sorted(PROCESS_OUTPUTS["ms"])),
    "PM": tuple(sorted(PROCESS_OUTPUTS["pm"])),
    "SL": tuple(sorted(PROCESS_OUTPUTS["sl"])),
}

# These definitions mirror the supplied PLC process structure. They are used
# for analytics, not for commanding the PLC. `elapsed` measures the real
# process window; `actuator` measures the time the named output was ON.
STAGE_DEFINITIONS = (
    # These are phase windows matching ProcessMonitor/PLC transitions. They
    # measure the complete observed stage, including waits between actuator
    # operations.
    {"stage": "Burning", "start": "rise:ms.process.burn", "end": "fall:ms.process.burn", "actuators": ("ms.process.burn",)},
    {"stage": "Oven release", "start": "fall:ms.process.burn", "end": "rise:ms.motor.transfer_oven", "actuators": ("ms.valve.oven_door", "ms.motor.slider_out")},
    {"stage": "Transfer from oven", "start": "rise:ms.motor.transfer_oven", "end": "rise:ms.motor.transfer_turntable", "actuators": ("ms.motor.transfer_oven", "ms.valve.transfer", "ms.valve.vacuum")},
    {"stage": "Transfer to turntable", "start": "rise:ms.motor.transfer_turntable", "end": "rise:ms.motor.turntable_cw", "actuators": ("ms.motor.transfer_turntable", "ms.valve.transfer", "ms.valve.vacuum")},
    {"stage": "Sawing", "start": "rise:ms.motor.turntable_cw", "end": "rise:ms.motor.conveyor", "actuators": ("ms.motor.turntable_cw", "ms.motor.saw", "ms.motor.ejector" if False else "ms.motor.turntable_cw")},
    {"stage": "Move to sorting", "start": "rise:ms.motor.conveyor", "end": "fall:ms.motor.conveyor", "actuators": ("ms.motor.conveyor", "sl.motor.conveyor")},
)

def _bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    text = series.astype(str).str.strip().str.lower()
    result = pd.Series(False, index=series.index)
    result[text.isin(BOOL_TRUE)] = True
    result[text.isin(BOOL_FALSE)] = False
    return result


def attach_source_timestamps(df: pd.DataFrame, timestamp_file: Path | None = None) -> pd.DataFrame:
    """Attach per-node OPC UA SourceTimestamps to telemetry by snapshot_id.

    Raw telemetry values are unchanged. When the timestamp sidecar is present,
    edge-based analytics can use the timestamp belonging to the signal that
    actually changed instead of the dashboard capture time.
    """
    if df.empty or timestamp_file is None or not timestamp_file.exists() or "snapshot_id" not in df.columns:
        return df
    ts = pd.read_csv(timestamp_file)
    if ts.empty or "snapshot_id" not in ts.columns:
        return df
    keep = [c for c in ts.columns if c == "snapshot_id" or c.endswith(".source_ts") or c in {"source_timestamp_reference", "sync_spread_ms"}]
    ts = ts[keep].drop_duplicates("snapshot_id", keep="last")
    out = df.merge(ts, on="snapshot_id", how="left", suffixes=("", "_sidecar"))
    if "source_timestamp_reference_sidecar" in out.columns:
        out["source_timestamp_reference"] = out["source_timestamp_reference"].fillna(out["source_timestamp_reference_sidecar"]) if "source_timestamp_reference" in out.columns else out["source_timestamp_reference_sidecar"]
        out = out.drop(columns=["source_timestamp_reference_sidecar"])
    if "sync_spread_ms_sidecar" in out.columns:
        if "sync_spread_ms" in out.columns:
            out["sync_spread_ms"] = out["sync_spread_ms"].fillna(out["sync_spread_ms_sidecar"])
        else:
            out["sync_spread_ms"] = out["sync_spread_ms_sidecar"]
        out = out.drop(columns=["sync_spread_ms_sidecar"])
    return out


def clean_telemetry(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize recorded telemetry into a plot-ready dataframe.

    The raw CSV remains untouched. This function creates a deterministic,
    timestamp-aligned analytical view with cleaned boolean/numeric columns and
    sample intervals.
    """
    if df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "timestamp" not in out.columns:
        raise ValueError("Telemetry requires a timestamp column")
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    if "source_timestamp_reference" in out.columns:
        out["source_timestamp_reference"] = pd.to_datetime(out["source_timestamp_reference"], utc=True, errors="coerce")
    for col in [c for c in out.columns if c.endswith(".source_ts")]:
        out[col] = pd.to_datetime(out[col], utc=True, errors="coerce")
    out = out.dropna(subset=["timestamp"])
    dedup_key = "snapshot_id" if "snapshot_id" in out.columns else "timestamp"
    out = out.drop_duplicates(subset=[dedup_key], keep="last").reset_index(drop=True)
    if "source_timestamp_reference" not in out.columns:
        source_cols = [c for c in out.columns if c.endswith(".source_ts")]
        if source_cols:
            out["source_timestamp_reference"] = out[source_cols].max(axis=1)
    if "source_timestamp_reference" in out.columns:
        out["analysis_timestamp"] = out["source_timestamp_reference"].fillna(out["timestamp"])
    else:
        out["analysis_timestamp"] = out["timestamp"]
    out = out.sort_values("analysis_timestamp", kind="stable").reset_index(drop=True)
    if "sync_spread_ms" not in out.columns:
        source_cols = [c for c in out.columns if c.endswith(".source_ts")]
        if source_cols:
            parsed = out[source_cols]
            out["sync_spread_ms"] = (parsed.max(axis=1) - parsed.min(axis=1)).dt.total_seconds() * 1000.0

    timestamp_columns = {"timestamp", "analysis_timestamp", "source_timestamp_reference"}
    timestamp_columns.update(c for c in out.columns if c.endswith(".source_ts") or c.endswith(".server_ts"))
    for col in out.columns:
        if col in timestamp_columns:
            continue
        non_null = out[col].dropna()
        if non_null.empty:
            continue
        unique = {str(v).strip().lower() for v in non_null.unique()[:100]}
        if unique and unique.issubset(BOOL_TRUE | BOOL_FALSE):
            out[col] = _bool_series(out[col])
        elif not pd.api.types.is_numeric_dtype(out[col]):
            numeric = pd.to_numeric(out[col], errors="coerce")
            if numeric.notna().sum() >= max(1, int(0.9 * len(non_null))):
                out[col] = numeric

    out["sample_interval_s"] = out["timestamp"].diff().dt.total_seconds()
    out["sample_interval_s"] = out["sample_interval_s"].clip(lower=0)
    out["sample_rate_hz"] = np.where(out["sample_interval_s"] > 0, 1.0 / out["sample_interval_s"], np.nan)

    # Region activity is explicitly based on physical outputs, not idle sensors.
    for region, signals in REGION_SIGNAL_MAP.items():
        available = [s for s in signals if s in out.columns]
        out[f"region.{region}.active"] = out[available].astype("boolean").fillna(False).any(axis=1) if available else False

    # Add semantic sensor columns without changing the raw PLC columns. The
    # supplied PLC program uses active-low light barriers: TRUE means clear,
    # FALSE means a workpiece is present.
    from .tags import SENSOR_ACTIVE_WHEN
    for key, active_when in SENSOR_ACTIVE_WHEN.items():
        if key in out.columns:
            raw = _bool_series(out[key])
            out[f"physical.{key}.active"] = raw.eq(active_when)

    return out


def _edge_times(df: pd.DataFrame, signal: str, rising: bool | None = None) -> list[pd.Timestamp]:
    if signal not in df.columns or df.empty:
        return []
    s = _bool_series(df[signal])
    changed = s.ne(s.shift(fill_value=s.iloc[0]))
    if rising is True:
        changed &= s
    elif rising is False:
        changed &= ~s
    time_col = f"{signal}.source_ts" if f"{signal}.source_ts" in df.columns else "analysis_timestamp" if "analysis_timestamp" in df.columns else "timestamp"
    return df.loc[changed, time_col].dropna().tolist()


def signal_intervals(df: pd.DataFrame, signal: str) -> pd.DataFrame:
    """Return observed ON intervals using source telemetry timestamps."""
    if signal not in df.columns or df.empty:
        return pd.DataFrame(columns=["signal", "start", "end", "duration_s", "complete"])
    s = _bool_series(df[signal]).reset_index(drop=True)
    time_col = f"{signal}.source_ts" if f"{signal}.source_ts" in df.columns else "analysis_timestamp" if "analysis_timestamp" in df.columns else "timestamp"
    ts = df[time_col].reset_index(drop=True)
    rows: list[dict] = []
    start: pd.Timestamp | None = None
    for i, on in enumerate(s):
        if on and start is None:
            start = ts.iloc[i]
        if not on and start is not None:
            end = ts.iloc[i]
            rows.append({"signal": signal, "start": start, "end": end, "duration_s": (end - start).total_seconds(), "complete": True})
            start = None
    if start is not None:
        rows.append({"signal": signal, "start": start, "end": pd.NaT, "duration_s": np.nan, "complete": False})
    return pd.DataFrame(rows)


def _union_duration(df: pd.DataFrame, signals: Iterable[str], start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Union ON-time of multiple actuators inside a stage window."""
    clipped: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for signal in signals:
        for _, row in signal_intervals(df, signal).iterrows():
            if pd.isna(row["end"]):
                continue
            a = max(start, row["start"])
            b = min(end, row["end"])
            if b > a:
                clipped.append((a, b))
    if not clipped:
        return 0.0
    clipped.sort()
    total = 0.0
    cur_a, cur_b = clipped[0]
    for a, b in clipped[1:]:
        if a <= cur_b:
            cur_b = max(cur_b, b)
        else:
            total += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
    total += (cur_b - cur_a).total_seconds()
    return total


def build_stage_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Build actual stage/operation measurements from telemetry transitions.

    Every row is an observed occurrence. `elapsed_s` is the complete stage
    window, while `actuator_on_s` is the actual ON time of the responsible
    output(s). This distinction exposes waiting/delay instead of hiding it.
    """
    if df.empty:
        return pd.DataFrame()
    out = clean_telemetry(df) if "sample_interval_s" not in df.columns else df
    rows: list[dict] = []
    burn_starts = _edge_times(out, "ms.process.burn", True)

    def nearest_burn(ts: pd.Timestamp) -> int | None:
        starts = [x for x in burn_starts if x <= ts]
        return len(starts) if starts else None

    # Direct actuator pulse metrics first. These are the most trustworthy
    # measurements because they are directly tied to an output's ON/OFF edges.
    direct = [
        ("Burning", "ms.process.burn"),
        ("Oven door", "ms.valve.oven_door"),
        ("Oven slider out", "ms.motor.slider_out"),
        ("Transfer from oven", "ms.motor.transfer_oven"),
        ("Transfer to turntable", "ms.motor.transfer_turntable"),
        ("Turntable clockwise", "ms.motor.turntable_cw"),
        ("Sawing", "ms.motor.saw"),
        ("MS conveyor", "ms.motor.conveyor"),
        ("Sorting conveyor", "sl.motor.conveyor"),
        ("Sorting white", "sl.valve.white"),
        ("Sorting red", "sl.valve.red"),
        ("Sorting blue", "sl.valve.blue"),
    ]
    for operation, signal in direct:
        for occurrence, row in enumerate(signal_intervals(out, signal).to_dict("records"), 1):
            rows.append({
                "measurement": "actuator_on",
                "stage": operation,
                "signal": signal,
                "occurrence": occurrence,
                "cycle_context": nearest_burn(row["start"]),
                "start": row["start"],
                "end": row["end"],
                "elapsed_s": row["duration_s"],
                "actuator_on_s": row["duration_s"],
                "complete": row["complete"],
            })

    # Complete process-phase windows. These are intentionally separate from
    # direct actuator pulses: a phase can contain waiting, clamping, vacuum
    # and positioning time that is invisible if we only measure motor ON time.
    for definition in STAGE_DEFINITIONS:
        stage = definition["stage"]
        start_spec = definition["start"]
        end_spec = definition["end"]
        _, start_sig = start_spec.split(":", 1)
        _, end_sig = end_spec.split(":", 1)
        starts = _edge_times(out, start_sig, start_spec.startswith("rise:"))
        ends = _edge_times(out, end_sig, end_spec.startswith("rise:"))
        for start_time in starts:
            candidates = [x for x in ends if x >= start_time]
            if not candidates:
                continue
            end_time = candidates[0]
            rows.append({
                "measurement": "stage_elapsed",
                "stage": stage,
                "signal": f"{start_spec} -> {end_spec}",
                "occurrence": len([r for r in rows if r["measurement"] == "stage_elapsed" and r["stage"] == stage]) + 1,
                "cycle_context": nearest_burn(start_time),
                "start": start_time,
                "end": end_time,
                "elapsed_s": (end_time - start_time).total_seconds(),
                "actuator_on_s": _union_duration(out, definition["actuators"], start_time, end_time),
                "complete": True,
            })

    # Sorting is a PLC sub-process which continues after the MS conveyor stops
    # and may overlap the next MS preparation. Its observed phase therefore
    # ends at the next oven-preparation transition if present, otherwise at the
    # next burn edge.
    conveyor_falls = _edge_times(out, "ms.motor.conveyor", False)
    prep_events = sorted(_edge_times(out, "ms.valve.oven_door", True) + _edge_times(out, "ms.motor.slider_in", True))
    for start_time in conveyor_falls:
        next_prep = [x for x in prep_events if x > start_time]
        next_burn = [x for x in burn_starts if x > start_time]
        candidates = [x for x in ((next_prep[:1] + next_burn[:1])) if x is not None]
        if not candidates:
            continue
        end_time = min(candidates)
        rows.append({
            "measurement": "stage_elapsed",
            "stage": "Sorting",
            "signal": "fall:ms.motor.conveyor -> next preparation/burn",
            "occurrence": len([r for r in rows if r["measurement"] == "stage_elapsed" and r["stage"] == "Sorting"]) + 1,
            "cycle_context": nearest_burn(start_time),
            "start": start_time,
            "end": end_time,
            "elapsed_s": (end_time - start_time).total_seconds(),
            "actuator_on_s": _union_duration(out, ("sl.valve.white", "sl.valve.red", "sl.valve.blue", "sl.motor.conveyor"), start_time, end_time),
            "complete": True,
        })
    return pd.DataFrame(rows)


def build_cycle_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Measure the real material-flow production cycle.

    Cycle start: vacuum gripper picks a workpiece from the HBW hand-off
    position (vacuum rising while the HBW outside light barrier reports a
    workpiece). Cycle end: that workpiece reaches the sorting-line entry
    light barrier (active-low ``sl.sensor.before_color``).

    The factory is pipelined, so starts and ends are paired FIFO and can
    overlap. Burn-to-burn timing is intentionally not used as production
    cycle time; burning remains a process-stage marker.
    """
    if df.empty or "c.valve.vacuum" not in df.columns or "sl.sensor.before_color" not in df.columns:
        return pd.DataFrame()
    out = clean_telemetry(df) if "sample_interval_s" not in df.columns else df

    def source_time(row_index, signal: str) -> pd.Timestamp:
        col = f"{signal}.source_ts"
        if col in out.columns:
            value = out.loc[row_index, col]
            if pd.notna(value):
                return pd.Timestamp(value)
        return pd.Timestamp(out.loc[row_index, "analysis_timestamp"])

    vacuum = _bool_series(out["c.valve.vacuum"])
    hbw_outside = _bool_series(out["hbw.sensor.outside"]) if "hbw.sensor.outside" in out.columns else pd.Series(True, index=out.index)
    pickup_edges = vacuum & ~vacuum.shift(1, fill_value=False) & ~hbw_outside

    sorting_raw = _bool_series(out["sl.sensor.before_color"])
    sorting_active = ~sorting_raw
    sorting_edges = sorting_active & ~sorting_active.shift(1, fill_value=False)

    starts = [(idx, source_time(idx, "c.valve.vacuum")) for idx in out.index[pickup_edges]]
    ends = [(idx, source_time(idx, "sl.sensor.before_color")) for idx in out.index[sorting_edges]]

    rows = []
    start_queue = list(starts)
    end_pos = 0
    cycle_number = 1
    for _, end_time in ends:
        if not start_queue:
            continue
        start_idx, start_time = start_queue.pop(0)
        if end_time <= start_time:
            continue
        rows.append({
            "cycle": cycle_number,
            "start": start_time,
            "end": end_time,
            "cycle_time_s": (end_time - start_time).total_seconds(),
            "start_event": "HBW pickup",
            "end_event": "Sorting-line entry",
            "start_signal": "c.valve.vacuum + hbw.sensor.outside active",
            "end_signal": "sl.sensor.before_color active",
        })
        cycle_number += 1

    # Keep the independently useful pickup-to-pickup interval as throughput
    # cadence, without confusing it with the material-flow cycle duration.
    for i, row in enumerate(rows):
        starts_before = [t for _, t in starts if t <= row["start"]]
        if len(starts_before) >= 2:
            row["pickup_interval_s"] = (starts_before[-1] - starts_before[-2]).total_seconds()

    return pd.DataFrame(rows)

def kpi_summary(telemetry: pd.DataFrame, cycles: pd.DataFrame, stages: pd.DataFrame, events: pd.DataFrame | None = None) -> dict:
    clean = clean_telemetry(telemetry)
    summary: dict = {
        "samples": int(len(clean)),
        "duration_s": float((clean["analysis_timestamp"].iloc[-1] - clean["analysis_timestamp"].iloc[0]).total_seconds()) if len(clean) > 1 and "analysis_timestamp" in clean.columns else 0.0,
        "cycles_completed": int(len(cycles)),
    }
    if not cycles.empty:
        vals = cycles["cycle_time_s"].dropna()
        summary.update({
            "cycle_avg_s": float(vals.mean()),
            "cycle_median_s": float(vals.median()),
            "cycle_min_s": float(vals.min()),
            "cycle_max_s": float(vals.max()),
            "cycle_std_s": float(vals.std(ddof=0)),
            "throughput_per_hour": float(3600.0 / vals.median()) if vals.median() > 0 else 0.0,
            "cycle_definition": "HBW pickup to sorting-line entry; pickup cadence shown separately",
        })
    else:
        summary.update({k: None for k in ("cycle_avg_s", "cycle_median_s", "cycle_min_s", "cycle_max_s", "cycle_std_s", "throughput_per_hour")})
    if not stages.empty:
        # Bottleneck means elapsed process-window time, not raw actuator ON
        # time. Direct actuator pulses remain available separately for
        # maintenance/operation analysis.
        stage_values = stages[stages["measurement"] == "stage_elapsed"][["stage", "elapsed_s"]].dropna().copy()
        if not stage_values.empty:
            med = stage_values.groupby("stage")["elapsed_s"].median()
            summary["bottleneck_stage"] = str(med.idxmax())
            summary["bottleneck_median_s"] = float(med.max())
        else:
            summary["bottleneck_stage"] = None
            summary["bottleneck_median_s"] = None
    else:
        summary["bottleneck_stage"] = None
        summary["bottleneck_median_s"] = None
    if events is not None and not events.empty:
        summary["critical_events"] = int((events["severity"] == "CRITICAL").sum()) if "severity" in events else 0
        summary["warning_events"] = int((events["severity"] == "WARNING").sum()) if "severity" in events else 0
    else:
        summary["critical_events"] = 0
        summary["warning_events"] = 0
    return summary


def read_session(session_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    telemetry_path = session_dir / "telemetry.csv"
    if not telemetry_path.exists():
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    telemetry = pd.read_csv(telemetry_path)
    telemetry = attach_source_timestamps(telemetry, session_dir / "telemetry_timestamps.csv")
    clean = clean_telemetry(telemetry)
    cycles = pd.read_csv(session_dir / "process_cycles.csv") if (session_dir / "process_cycles.csv").exists() else build_cycle_metrics(clean)
    stages = pd.read_csv(session_dir / "stage_operations.csv") if (session_dir / "stage_operations.csv").exists() else build_stage_metrics(clean)
    for frame in (cycles, stages):
        for col in ("start", "end"):
            if col in frame.columns:
                frame[col] = pd.to_datetime(frame[col], utc=True, errors="coerce")
    return clean, cycles, stages


def export_analytics(session_dir: Path) -> None:
    telemetry_path = session_dir / "telemetry.csv"
    if not telemetry_path.exists():
        return
    telemetry = pd.read_csv(telemetry_path)
    telemetry = attach_source_timestamps(telemetry, session_dir / "telemetry_timestamps.csv")
    clean = clean_telemetry(telemetry)
    cycles = build_cycle_metrics(clean)
    stages = build_stage_metrics(clean)
    clean.to_csv(session_dir / "telemetry_clean.csv", index=False)
    cycles.to_csv(session_dir / "cycle_metrics.csv", index=False)
    stages.to_csv(session_dir / "stage_operations.csv", index=False)
    events = pd.DataFrame()
    events_path = session_dir / "events.jsonl"
    if events_path.exists():
        rows = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        events = pd.DataFrame(rows)
    (session_dir / "kpi_summary.json").write_text(json.dumps(kpi_summary(clean, cycles, stages, events), indent=2, default=str), encoding="utf-8")
