from __future__ import annotations

from pathlib import Path
import sys

# Streamlit executes app.py as a script. Add the src directory explicitly so
# package imports work reliably with both `uv run streamlit run ...` and direct runs.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from factory_dashboard.config import ASSETS, DATA, SETTINGS
from factory_dashboard.data_source import ReplayDataSource
from factory_dashboard.history import History
from factory_dashboard.recorder import TelemetryRecorder
from factory_dashboard.logic import color_class, emergency_source, fault_summary, process_phase, region_statuses
from factory_dashboard.models import Health, Snapshot, SourceMode
from factory_dashboard.opcua_client import OpcUaReader
from factory_dashboard.simulation import FactorySimulation
from factory_dashboard.visualization import factory_figure, trend_figure


st.set_page_config(page_title="Fischertechnik Factory Monitor", page_icon="🏭", layout="wide")


@st.cache_data(show_spinner=False)
def tag_descriptions() -> dict[str, str]:
    from factory_dashboard.tags import TAGS
    return {key: tag.description for key, tag in TAGS.items()}


def get_sim() -> FactorySimulation:
    if "sim" not in st.session_state:
        st.session_state.sim = FactorySimulation()
    return st.session_state.sim


def get_history() -> History:
    if "history" not in st.session_state:
        st.session_state.history = History(max_rows=max(600, SETTINGS.history_seconds * 2))
    return st.session_state.history


def get_reader() -> OpcUaReader:
    if "reader" not in st.session_state:
        reader = OpcUaReader(
            SETTINGS.opcua_url,
            SETTINGS.opcua_namespace,
            SETTINGS.poll_interval,
            SETTINGS.connect_timeout,
            SETTINGS.stale_after,
        )
        reader.start()
        st.session_state.reader = reader
    return st.session_state.reader


def get_replay(path: Path) -> ReplayDataSource:
    if "replay_source" not in st.session_state or st.session_state.get("replay_path") != str(path):
        st.session_state.replay_source = ReplayDataSource(path, speed=SETTINGS.replay_speed)
        st.session_state.replay_path = str(path)
    return st.session_state.replay_source



def get_recorder() -> TelemetryRecorder:
    if "recorder" not in st.session_state:
        st.session_state.recorder = TelemetryRecorder(DATA / "recordings")
    return st.session_state.recorder


def latest_replay_file() -> Path | None:
    files = list((DATA / "recordings").glob("*.csv")) + list(DATA.glob("*.csv"))
    files = [p for p in files if p.is_file() and p.stat().st_size > 0]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def record_live_snapshot(snap: Snapshot) -> None:
    if SETTINGS.record_live and snap.source == SourceMode.LIVE and snap.good_count > 0:
        get_recorder().record(snap)

def reset_history_if_source_changed(mode: str) -> None:
    previous = st.session_state.get("previous_mode")
    if previous != mode:
        st.session_state.history = History(max_rows=max(600, SETTINGS.history_seconds * 2))
        st.session_state.previous_mode = mode


def acquire(mode: str) -> Snapshot:
    replay_file = latest_replay_file()

    if mode == SourceMode.SIMULATION.value:
        return get_sim().snapshot()

    if mode == SourceMode.REPLAY.value:
        if replay_file:
            return get_replay(replay_file).snapshot()
        sim = get_sim().snapshot()
        sim.message = "No replay CSV found; showing simulation instead."
        return sim

    live = get_reader().snapshot()
    if live.connected and live.good_count > 0:
        record_live_snapshot(live)

    if mode == SourceMode.LIVE.value:
        return live

    # Auto mode: prefer healthy live data, then the newest recording, then simulation.
    if live.connected and live.health not in {Health.ERROR, Health.STALE}:
        return live
    if replay_file:
        replay = get_replay(replay_file).snapshot()
        replay.message = f"AUTO fallback: live OPC UA unavailable · {replay.message}"
        return replay
    sim = get_sim().snapshot()
    sim.message = "AUTO fallback: live OPC UA unavailable and no recording found · showing simulation."
    return sim


def source_badge(snap: Snapshot) -> str:
    if snap.source == SourceMode.LIVE:
        if snap.emergency:
            return "🔴 LIVE OPC UA · EMERGENCY"
        if snap.health == Health.STALE:
            return "🟠 LIVE OPC UA · STALE / LAST KNOWN"
        if not snap.connected:
            return "🔴 LIVE OPC UA · OFFLINE"
        return "🟢 LIVE OPC UA"
    if snap.source == SourceMode.REPLAY:
        return "🟡 REPLAY · RECORDED DATA"
    return "🔵 SIMULATION · DUMMY DATA"


def render_status(snap: Snapshot) -> None:
    if snap.emergency:
        st.error(
            "🚨 EMERGENCY STOP ACTIVE — The dashboard is read-only. "
            "Do not use this application to reset or restart the plant. Follow the physical plant safety procedure."
        )
    elif snap.source == SourceMode.LIVE and snap.health == Health.STALE:
        st.warning(
            f"⚠️ LIVE CONNECTION LOST / DATA STALE — showing the last known telemetry. "
            f"Last good update: {snap.last_good_timestamp}."
        )
    elif snap.source == SourceMode.LIVE and not snap.connected:
        st.error(f"🔴 OPC UA OFFLINE — {snap.message}")
    elif snap.source == SourceMode.REPLAY:
        st.warning("🟡 This is recorded telemetry, not the live factory.")
    elif snap.source == SourceMode.SIMULATION:
        st.info("🔵 Simulation mode — no connection to the factory and no PLC writes are performed.")


st.title("Fischertechnik Learning Factory · Process Monitor")
st.caption(
    "Read-only virtualisation: PLC/OPC UA values are visualised; the dashboard never commands actuators."
)

with st.sidebar:
    st.header("Data source")
    modes = ["Auto", SourceMode.LIVE.value, SourceMode.SIMULATION.value, SourceMode.REPLAY.value]
    mode = st.radio("Mode", modes, index=0)
    reset_history_if_source_changed(mode)
    st.caption(f"OPC UA: `{SETTINGS.opcua_url}` · namespace `{SETTINGS.opcua_namespace}`")
    refresh = st.slider("Refresh (ms)", 500, 5000, SETTINGS.refresh_ms, 250)

    if mode == SourceMode.SIMULATION.value:
        sim = get_sim()
        c1, c2 = st.columns(2)
        if c1.button("Pause / Run", use_container_width=True):
            sim.toggle()
            st.rerun()
        if c2.button("Reset", use_container_width=True):
            sim.reset()
            st.rerun()
    elif mode == SourceMode.REPLAY.value:
        replay_file = latest_replay_file()
        st.write("Latest recording:", replay_file.name if replay_file else "None found")
        if replay_file and st.button("Restart replay", use_container_width=True):
            get_replay(replay_file).reset()
            st.rerun()
        st.caption(f"Timestamp-aware replay at {SETTINGS.replay_speed:g}× speed. The newest recording is selected automatically.")

    if SETTINGS.record_live:
        st.caption("Live recording: enabled")
    else:
        st.caption("Live recording: disabled")

st_autorefresh(interval=refresh, key="dashboard_refresh")
snap = acquire(mode)
render_status(snap)

regions = region_statuses(snap)
phase = process_phase(snap)
history = get_history()
history.add(snap)

# Compact status header.
st.markdown(f"### {source_badge(snap)}")

active_count = sum(r.active for r in regions)
status_label = (
    "EMERGENCY" if snap.emergency else
    "STALE" if snap.health == Health.STALE else
    "CONNECTED" if snap.connected else "OFFLINE"
)
cycle = snap.get("sim.cycle", None)
cycle_display = int(cycle) if cycle is not None else "—"

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Connection", status_label)
c2.metric("Process phase", phase)
c3.metric("Active regions", f"{active_count}/{len(regions)}")
c4.metric("Burning", "ON" if snap.bool("ms.process.burn") else "OFF")
c5.metric("Cycle", cycle_display)
c6.metric("Good / Bad", f"{snap.good_count} / {snap.bad_count}")

left, right = st.columns([1.8, 1])
with left:
    st.subheader("Virtual factory")
    st.plotly_chart(
        factory_figure(ASSETS / "factory_overview.jpg", regions),
        use_container_width=True,
        config={"displayModeBar": False},
    )
with right:
    st.subheader("Station status")
    for region in regions:
        if region.fault:
            icon = "🔴"
            state = "FAULT / EMERGENCY"
        elif region.active:
            icon = "🟢"
            state = "ACTIVE"
        else:
            icon = "⚪"
            state = "IDLE"
        st.write(f"{icon} **{region.label}** · {state}")
        st.caption(region.detail)

    st.divider()
    st.write("**Emergency source:**", emergency_source(snap))
    st.write("**Color:**", color_class(snap.get("sl.sensor.color_value")))
    crane_coords = (snap.get("local.crane_coord_h", None), snap.get("local.crane_coord_v", None), snap.get("local.crane_coord_r", None))
    if all(v is not None for v in crane_coords):
        st.write("**Crane H / V / R:**", f"{crane_coords[0]} / {crane_coords[1]} / {crane_coords[2]}")
    wp_coord = snap.get("local.sl_workpiece_coord", None)
    if wp_coord is not None:
        st.write("**Sorting-line WP coordinate:**", wp_coord)
    if snap.last_good_timestamp:
        st.write("**Last good update:**", snap.last_good_timestamp)
    st.write("**Source:**", snap.message)

faults = fault_summary(snap)
if faults:
    st.subheader("Diagnostics")
    for fault in faults:
        st.write(f"• {fault}")

# Process timeline / current phase.
st.subheader("Process reference")
st.caption("Sequence derived from the supplied PLC program. Simulation uses shortened demonstration timings; live values determine the actual current state.")
phase_rows = []
for idx, (name, duration) in enumerate(FactorySimulation.PHASES):
    phase_rows.append({"Step": idx + 1, "Phase": name, "Duration (s)": duration,
                       "Current": name == phase})
st.dataframe(pd.DataFrame(phase_rows), use_container_width=True, hide_index=True)

st.subheader("Live process data")
df = history.dataframe()

plot_cols = [
    "ms.process.burn", "ms.motor.saw", "ms.motor.conveyor", "ms.motor.slider_in",
    "ms.motor.slider_out", "sl.motor.conveyor", "hbw.motor.crane_rack", "c.motor.forward",
]
if not df.empty:
    st.plotly_chart(trend_figure(df, plot_cols, "Actuator activity"), use_container_width=True)

c1, c2 = st.columns(2)
with c1:
    sensor_cols = [
        "ms.sensor.oven", "ms.sensor.conveyor", "sl.sensor.before_color", "sl.sensor.after_color",
    ]
    if not df.empty:
        st.plotly_chart(trend_figure(df, sensor_cols, "Process sensors"), use_container_width=True)
with c2:
    if not df.empty and "sl.sensor.color_value" in df.columns:
        st.plotly_chart(
            trend_figure(df, ["sl.sensor.color_value"], "Color sensor value"),
            use_container_width=True,
        )
    else:
        st.info("Color sensor history becomes available when `gvl_SL.iColorSensor_SL` is exposed.")

with st.expander("Current variables / diagnostics", expanded=False):
    descriptions = tag_descriptions()
    current = pd.DataFrame([
        {
            "Tag": key,
            "Value": value,
            "Status": snap.statuses.get(key, "derived"),
            "Description": descriptions.get(key, "Simulation / derived value"),
        }
        for key, value in sorted(snap.values.items())
    ])
    st.dataframe(current, use_container_width=True, hide_index=True)

with st.expander("OPC UA connection diagnostics", expanded=False):
    st.write(f"**Endpoint:** `{SETTINGS.opcua_url}`")
    st.write(f"**Namespace:** `{SETTINGS.opcua_namespace}`")
    st.write(f"**Connection:** {status_label}")
    st.write(f"**Values received:** {len(snap.values)}")
    st.write(f"**Good:** {snap.good_count} · **Bad:** {snap.bad_count}")
    if snap.last_good_timestamp:
        st.write(f"**Last good update:** {snap.last_good_timestamp}")
    bad = {k: v for k, v in snap.statuses.items() if not str(v).lower().startswith("good")}
    if bad:
        st.dataframe(
            pd.DataFrame([{"Tag": k, "Status": v} for k, v in sorted(bad.items())]),
            use_container_width=True,
            hide_index=True,
        )

recorder = st.session_state.get("recorder")
if recorder and recorder.path:
    st.caption(f"Current live recording: `{recorder.path.relative_to(DATA)}`")

st.caption(
    "Safety: read-only OPC UA access. No write nodes, start/stop commands, emergency reset, or actuator control are implemented."
)
