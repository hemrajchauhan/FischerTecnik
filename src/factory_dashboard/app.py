from __future__ import annotations

# Streamlit executes this file as a script. Bootstrap the src directory so
# package-relative imports work with `uv run streamlit run .../app.py`.
import sys
from pathlib import Path

if __package__ in (None, ""):
    _SRC_DIR = Path(__file__).resolve().parents[1]
    if str(_SRC_DIR) not in sys.path:
        sys.path.insert(0, str(_SRC_DIR))
    __package__ = "factory_dashboard"

import json
from datetime import datetime, time, timezone

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from .analytics import attach_source_timestamps, build_cycle_metrics, build_stage_metrics, clean_telemetry, kpi_summary
from .config import ASSETS, DATA, SETTINGS
from .data_source import ReplayDataSource
from .event_engine import Event, EventEngine
from .history import History
from .logic import emergency_source, fault_summary, process_phase, region_statuses, sorting_color
from .models import Health, Snapshot, SourceMode
from .opcua_client import OpcUaReader
from .recorder import TelemetryRecorder
from .simulation import FactorySimulation
from .visualization import (
    cycle_time_figure,
    event_timeline_figure,
    factory_figure,
    stage_duration_figure,
    station_activity_figure,
    trend_figure,
)

st.set_page_config(page_title="Fischertechnik Factory Monitor", page_icon="🏭", layout="wide")


@st.cache_data(show_spinner=False)
def tag_descriptions() -> dict[str, str]:
    from .tags import TAGS
    return {key: tag.description for key, tag in TAGS.items()}


def get_sim() -> FactorySimulation:
    if "sim" not in st.session_state:
        st.session_state.sim = FactorySimulation()
    return st.session_state.sim


def get_history() -> History:
    if "history" not in st.session_state:
        st.session_state.history = History(max_rows=max(1200, SETTINGS.history_seconds * 4))
    return st.session_state.history


def get_live_history() -> History:
    if "live_history" not in st.session_state:
        st.session_state.live_history = History(max_rows=max(1200, SETTINGS.history_seconds * 4))
    return st.session_state.live_history


def get_replay_history() -> History:
    if "replay_history" not in st.session_state:
        st.session_state.replay_history = History(max_rows=max(1200, SETTINGS.history_seconds * 4))
    return st.session_state.replay_history


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
    if st.session_state.get("replay_path") != str(path):
        st.session_state.replay_source = ReplayDataSource(path)
        st.session_state.replay_path = str(path)
    return st.session_state.replay_source


def get_event_engine() -> EventEngine:
    if "event_engine" not in st.session_state:
        st.session_state.event_engine = EventEngine(
            SETTINGS.process_watchdog_seconds, SETTINGS.opcua_sync_warning_ms
        )
    return st.session_state.event_engine


def get_live_event_engine() -> EventEngine:
    if "live_event_engine" not in st.session_state:
        st.session_state.live_event_engine = EventEngine(
            SETTINGS.process_watchdog_seconds, SETTINGS.opcua_sync_warning_ms
        )
    return st.session_state.live_event_engine


def get_recorder() -> TelemetryRecorder:
    if "recorder" not in st.session_state:
        st.session_state.recorder = TelemetryRecorder(
            DATA / "recordings", SETTINGS.incident_pre_seconds, SETTINGS.incident_post_seconds
        )
    return st.session_state.recorder


def reset_history_if_source_changed(mode: str) -> None:
    """Reset only the display pipeline when the selected source changes.

    The live acquisition/recording pipeline is deliberately never reset by a
    dashboard navigation or mode switch. This prevents duplicate recording and
    preserves the live PLC stream while a user is reviewing a recording.
    """
    previous = st.session_state.get("previous_mode")
    if previous == mode:
        return
    st.session_state.previous_mode = mode
    if mode == SourceMode.REPLAY.value:
        st.session_state.replay_history = History(max_rows=max(1200, SETTINGS.history_seconds * 4))
        st.session_state.event_engine = EventEngine(
            SETTINGS.process_watchdog_seconds, SETTINGS.opcua_sync_warning_ms
        )
        st.session_state.events = []
    elif mode == SourceMode.SIMULATION.value:
        st.session_state.history = History(max_rows=max(1200, SETTINGS.history_seconds * 4))
        st.session_state.event_engine = EventEngine(
            SETTINGS.process_watchdog_seconds, SETTINGS.opcua_sync_warning_ms
        )
        st.session_state.events = []
    else:
        st.session_state.history = get_live_history()
        st.session_state.event_engine = get_live_event_engine()
        st.session_state.events = st.session_state.get("live_events", [])


def replay_files() -> list[Path]:
    files = list((DATA / "recordings").glob("session_*/telemetry.csv")) + list(DATA.glob("*.csv"))
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def session_dirs() -> list[Path]:
    root = DATA / "recordings"
    if not root.exists():
        return []
    return sorted((p for p in root.glob("session_*") if (p / "telemetry.csv").exists()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def session_label(path: Path) -> str:
    meta = path / "metadata.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            start = data.get("started_at") or data.get("start")
            end = data.get("ended_at") or data.get("end")
            if start:
                start_dt = pd.to_datetime(start, utc=True, errors="coerce")
                if not pd.isna(start_dt):
                    label = start_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                    if end:
                        end_dt = pd.to_datetime(end, utc=True, errors="coerce")
                        if not pd.isna(end_dt):
                            label += f" → {end_dt.strftime('%H:%M:%S UTC')}"
                    return f"{path.name} · {label}"
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return path.name


def incident_records(session: Path) -> list[dict]:
    records: list[dict] = []
    roots = [session / "incidents", DATA / "incidents"]
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for meta_path in root.glob("*/metadata.json"):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("session") != session.name:
                continue
            incident = data.get("event", {})
            incident_id = data.get("incident_id") or meta_path.parent.name
            if incident_id in seen:
                continue
            seen.add(incident_id)
            timestamp = pd.to_datetime(incident.get("timestamp"), utc=True, errors="coerce")
            records.append({
                "id": incident_id,
                "timestamp": timestamp,
                "severity": incident.get("severity", ""),
                "code": incident.get("code", ""),
                "category": incident.get("category", ""),
                "message": incident.get("message", ""),
                "path": meta_path.parent,
            })
    return sorted(records, key=lambda r: r["timestamp"] if not pd.isna(r["timestamp"]) else pd.Timestamp.min, reverse=True)


def replay_source() -> ReplayDataSource | None:
    path = st.session_state.get("replay_selected_path")
    if not path:
        return None
    return get_replay(Path(path))


def seek_replay_to_timestamp(path: Path, timestamp: datetime) -> None:
    replay = get_replay(path)
    replay.seek_timestamp(timestamp, after=True)
    st.session_state["replay_playing"] = False


def acquire(mode: str) -> Snapshot:
    files = replay_files()
    if mode == SourceMode.SIMULATION.value:
        return get_sim().snapshot()
    if mode == SourceMode.REPLAY.value:
        selected = st.session_state.get("replay_selected_path")
        path = Path(selected) if selected else (files[0] if files else None)
        if path and path.exists():
            return get_replay(path).snapshot()
        sim = get_sim().snapshot()
        sim.message = "No replay CSV found; showing simulation instead."
        return sim
    reader = get_reader()
    live = reader.snapshot()
    if mode == SourceMode.LIVE.value:
        return live
    # Once a live reader has acquired data, Auto stays on the live source even
    # through temporary disconnect/stale periods. Switching to replay here
    # makes the virtual factory jump to an unrelated historical state and also
    # interrupts the live recording session.
    if reader.has_live_data:
        return live
    if files:
        replay = get_replay(files[0]).snapshot()
        replay.message = f"AUTO fallback: live OPC UA has not produced data yet · {replay.message}"
        return replay
    sim = get_sim().snapshot()
    sim.message = "AUTO fallback: live OPC UA has not produced data and no replay CSV exists · showing simulation."
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
        st.error("🚨 EMERGENCY STOP ACTIVE — dashboard is read-only. Follow the physical plant safety procedure.")
    elif snap.source == SourceMode.LIVE and snap.health == Health.STALE:
        st.warning(f"⚠️ LIVE CONNECTION LOST / DATA STALE — Last good update: {snap.last_good_timestamp}.")
    elif snap.source == SourceMode.LIVE and not snap.connected:
        st.error(f"🔴 OPC UA OFFLINE — {snap.message}")
    elif snap.source == SourceMode.REPLAY:
        st.warning("🟡 Recorded telemetry, not the live factory.")
    elif snap.source == SourceMode.SIMULATION:
        st.info("🔵 Simulation mode — no PLC writes are performed.")


def load_events(path: Path | None = None) -> pd.DataFrame:
    if path is None:
        rows = [e.__dict__ for e in st.session_state.get("events", [])]
    else:
        event_path = path / "events.jsonl"
        rows = []
        if event_path.exists():
            for line in event_path.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if not rows:
        return pd.DataFrame(columns=["timestamp", "category", "severity", "code", "message", "recovered"])
    df = pd.DataFrame(rows)
    if "timestamp" in df:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df


def analysis_data(snap: Snapshot, history_df: pd.DataFrame, selected_session: Path | None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if selected_session is not None and selected_session.exists():
        path = selected_session / "telemetry.csv"
        if path.exists():
            telemetry = pd.read_csv(path)
            telemetry = attach_source_timestamps(telemetry, selected_session / "telemetry_timestamps.csv")
            clean = clean_telemetry(telemetry)
            cycles = build_cycle_metrics(clean)
            stages = build_stage_metrics(clean)
            return clean, cycles, stages, load_events(selected_session)
    clean = clean_telemetry(history_df) if not history_df.empty else pd.DataFrame()
    cycles = build_cycle_metrics(clean)
    stages = build_stage_metrics(clean)
    return clean, cycles, stages, load_events(None)


def fmt_seconds(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    if value >= 60:
        return f"{value / 60:.1f} min"
    return f"{value:.1f} s"


def inject_visual_identity() -> None:
    """Apply the visual system from DASHBOARD_SPEC_1.md.

    This pass intentionally contains no analytical charts. It focuses on the
    shell, status banner, KPI cards, factory visualisation and station cards.
    """
    st.markdown(
        """
        <style>
        :root { --tum-blue:#0065BD; --light-blue:#98C6EA; --orange:#F28C28; --orange-dark:#B34700; --green:#2E8B57; --grey:#B0B0B5; --ink:#1D1D1F; --muted:#595960; --grid:#E5E5EA; }
        html, body, [class*="css"] { font-family: Arial, sans-serif; }
        .block-container { padding-top: 1.2rem; padding-bottom: 2.5rem; max-width: 1500px; }
        [data-testid="stSidebar"] { background:#F7F8FA; border-right:1px solid var(--grid); }
        .identity-eyebrow { color:var(--tum-blue); font-size:.82rem; font-weight:700; letter-spacing:.09em; text-transform:uppercase; margin-bottom:.15rem; }
        .identity-title { color:var(--ink); font-size:2rem; line-height:1.12; font-weight:700; margin:0; }
        .identity-subtitle { color:var(--muted); font-size:1rem; margin-top:.45rem; margin-bottom:1.05rem; }
        .source-pill { display:inline-block; background:#F2F4F7; color:var(--ink); border:1px solid var(--grid); border-radius:999px; padding:.45rem .75rem; font-size:.82rem; font-weight:700; white-space:nowrap; }
        .status-banner { border-radius:20px; padding:1rem 1.15rem; margin:.2rem 0 1rem; display:flex; align-items:center; justify-content:space-between; gap:1rem; border:1px solid transparent; }
        .status-banner .state { font-size:1.15rem; font-weight:800; letter-spacing:.02em; }
        .status-banner .meta { color:var(--muted); font-size:.92rem; text-align:right; }
        .status-live { background:#EAF3FB; border-color:#C9E2F5; } .status-live .state { color:var(--tum-blue); }
        .status-stop,.status-alert { background:#FFF2E8; border-color:#F7D1B2; } .status-stop .state,.status-alert .state { color:var(--orange-dark); }
        .status-idle { background:#F2F2F4; border-color:#DEDEE2; } .status-idle .state { color:var(--muted); }
        .status-replay { background:#EEF6FD; border-color:#C9E2F5; } .status-replay .state { color:var(--tum-blue); }
        .kpi-card { background:#fff; border:1px solid var(--grid); border-radius:18px; padding:1rem 1rem .9rem; min-height:118px; box-shadow:0 4px 18px rgba(29,31,33,.06); }
        .kpi-label { color:var(--muted); font-size:.73rem; font-weight:800; letter-spacing:.07em; text-transform:uppercase; margin-bottom:.35rem; }
        .kpi-value { color:var(--ink); font-size:2.05rem; line-height:1.05; font-weight:700; }
        .kpi-value.attention { color:var(--orange-dark); } .kpi-sub { color:var(--muted); font-size:.82rem; margin-top:.45rem; }
        .section-title { color:var(--ink); font-size:1.18rem; font-weight:700; margin:.4rem 0 .25rem; }
        .section-note { color:var(--muted); font-size:.88rem; margin-bottom:.7rem; }
        .station-card { background:#fff; border:1px solid var(--grid); border-radius:16px; padding:.85rem .9rem; margin-bottom:.55rem; box-shadow:0 3px 14px rgba(29,31,33,.045); }
        .station-top { display:flex; justify-content:space-between; align-items:center; gap:.5rem; }
        .station-name { color:var(--ink); font-weight:700; font-size:.94rem; }
        .station-state { font-size:.72rem; font-weight:800; letter-spacing:.04em; }
        .station-detail { color:var(--muted); font-size:.77rem; margin-top:.35rem; line-height:1.35; }
        .dot-active { color:var(--tum-blue); } .dot-idle { color:var(--grey); } .dot-alert { color:var(--orange-dark); }
        .factory-frame { background:#fff; border:1px solid var(--grid); border-radius:20px; padding:.55rem; box-shadow:0 4px 18px rgba(29,31,33,.06); }
        .identity-footer { color:#7A7A80; font-size:.76rem; margin-top:.9rem; }
        div[data-testid="stMetric"] { background:#fff; border:1px solid var(--grid); border-radius:18px; padding:.7rem .8rem; box-shadow:0 4px 18px rgba(29,31,33,.05); }
        </style>
        """,
        unsafe_allow_html=True,
    )


def role_title(view: str) -> tuple[str, str]:
    return {
        "Operating Manager": ("Operating Manager", "Live factory cockpit"),
        "Line Manager": ("Line Manager", "Line performance and station state"),
        "General Management": ("General Management", "Operations overview"),
    }[view]


def source_pill(snap: Snapshot) -> str:
    label = "LIVE" if snap.source == SourceMode.LIVE else f"REPLAY {st.session_state.get('replay_speed', 1)}×" if snap.source == SourceMode.REPLAY else "SIMULATION"
    stamp = snap.source_timestamp_reference or snap.timestamp
    ts = pd.to_datetime(stamp, utc=True, errors="coerce")
    time_text = ts.strftime("%H:%M:%S UTC") if not pd.isna(ts) else "—"
    return f"{label} · {time_text}"


def render_identity_header(view: str, snap: Snapshot) -> None:
    title, subtitle = role_title(view)
    st.markdown(
        f'''<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;">
        <div><div class="identity-eyebrow">SMART FACTORY · {title.upper()}</div>
        <div class="identity-title">{subtitle}</div>
        <div class="identity-subtitle">Read-only virtualisation of the Fischertechnik production line.</div></div>
        <div class="source-pill">{source_pill(snap)}</div></div>''',
        unsafe_allow_html=True,
    )


def render_status_banner(snap: Snapshot, process_state) -> None:
    if snap.emergency:
        css, state = "status-alert", "EMERGENCY STOP ACTIVE"
    elif snap.source == SourceMode.LIVE and not snap.connected:
        css, state = "status-alert", "NO LIVE DATA"
    elif snap.source == SourceMode.LIVE and snap.health == Health.STALE:
        css, state = "status-alert", "LIVE DATA STALE"
    elif snap.source == SourceMode.REPLAY:
        css, state = "status-replay", "RECORDED REPLAY"
    elif process_state.phase == "Idle / waiting":
        css, state = "status-idle", "LINE IDLE"
    elif snap.source == SourceMode.SIMULATION:
        css, state = "status-live", "SIMULATION RUNNING"
    else:
        css, state = "status-live", "FACTORY ACTIVE"
    elapsed = fmt_seconds(getattr(process_state, "phase_elapsed_s", None))
    detail = f"Stage: {process_state.phase} · Stage elapsed: {elapsed}"
    st.markdown(f'''<div class="status-banner {css}"><div class="state">{state}</div><div class="meta">{detail}</div></div>''', unsafe_allow_html=True)


def identity_kpi(label: str, value: str, sub: str = "", attention: bool = False) -> str:
    cls = "kpi-value attention" if attention else "kpi-value"
    return f'''<div class="kpi-card"><div class="kpi-label">{label}</div><div class="{cls}">{value}</div><div class="kpi-sub">{sub}</div></div>'''


def render_identity_kpis(view: str, summary: dict, snap: Snapshot, process_state) -> None:
    regions = region_statuses(snap)
    active = sum(r.active for r in regions)
    warnings = int(summary.get("warning_events", 0) or 0)
    critical = int(summary.get("critical_events", 0) or 0)
    if view == "Operating Manager":
        cards = [
            identity_kpi("Cycles completed", str(summary.get("cycles_completed", 0)), "Completed production cycles"),
            identity_kpi("Current stage", process_state.phase, f"Elapsed {fmt_seconds(process_state.phase_elapsed_s)}"),
            identity_kpi("Active regions", f"{active}/5", "Physical process outputs"),
            identity_kpi("Events", str(warnings + critical), f"{warnings} warnings · {critical} critical", attention=critical > 0),
        ]
    elif view == "Line Manager":
        cards = [
            identity_kpi("Cycles completed", str(summary.get("cycles_completed", 0)), "Production flow"),
            identity_kpi("Median cycle", fmt_seconds(summary.get("cycle_median_s")), "Pickup to sorting line"),
            identity_kpi("Bottleneck", summary.get("bottleneck_stage") or "—", "Observed stage duration"),
            identity_kpi("Active regions", f"{active}/5", "Current physical activity"),
        ]
    else:
        cards = [
            identity_kpi("Cycles completed", str(summary.get("cycles_completed", 0)), "Production output"),
            identity_kpi("Throughput", f"{summary.get('throughput_per_hour', 0):.1f}/h" if summary.get("throughput_per_hour") else "—", "Observed production rate"),
            identity_kpi("Warnings", str(warnings), "Recorded process/system warnings", attention=warnings > 0),
            identity_kpi("Critical events", str(critical), "Requires immediate review", attention=critical > 0),
        ]
    cols = st.columns(len(cards))
    for col, card in zip(cols, cards):
        with col:
            st.markdown(card, unsafe_allow_html=True)


def render_factory_identity(snap: Snapshot) -> None:
    regions = region_statuses(snap)
    st.markdown('<div class="section-title">Factory virtualisation</div><div class="section-note">Physical process outputs drive highlighted regions. Sensor/reference states remain diagnostics.</div>', unsafe_allow_html=True)
    left, right = st.columns([1.75, 1])
    with left:
        st.markdown('<div class="factory-frame">', unsafe_allow_html=True)
        st.plotly_chart(factory_figure(ASSETS / "factory_overview.jpg", regions), use_container_width=True, config={"displayModeBar": False})
        st.markdown('</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="section-title">Station status</div>', unsafe_allow_html=True)
        for region in regions:
            if region.fault:
                dot, state, cls = "●", "ATTENTION", "dot-alert"
            elif region.active:
                dot, state, cls = "●", "ACTIVE", "dot-active"
            else:
                dot, state, cls = "●", "IDLE", "dot-idle"
            st.markdown(f'''<div class="station-card"><div class="station-top"><div class="station-name">{region.label}</div><div class="station-state {cls}">{dot} {state}</div></div><div class="station-detail">{region.detail}</div></div>''', unsafe_allow_html=True)


def render_role_visual(view: str, snap, process_state, clean, cycles, stages, events):
    summary = kpi_summary(clean, cycles, stages, events)
    render_identity_header(view, snap)
    render_status_banner(snap, process_state)
    render_identity_kpis(view, summary, snap, process_state)
    render_factory_identity(snap)

    if view == "Operating Manager":
        st.markdown('<div class="section-title">What needs attention</div>', unsafe_allow_html=True)
        if events.empty:
            st.markdown('<div class="station-card"><div class="station-name">No recorded events</div><div class="station-detail">No process or system events in the selected window.</div></div>', unsafe_allow_html=True)
        else:
            recent = events.tail(5).copy()
            cols = [c for c in ["timestamp", "severity", "code", "message"] if c in recent.columns]
            st.dataframe(recent[cols], use_container_width=True, hide_index=True)
    elif view == "Line Manager":
        st.markdown('<div class="section-title">Current process state</div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        c1.metric("Burning", "ON" if snap.bool("ms.process.burn") else "OFF")
        c2.metric("Cycle", str(process_state.cycle) if process_state.cycle else "—")
        history_df = get_replay_history().dataframe() if snap.source == SourceMode.REPLAY else get_live_history().dataframe()
        c3.metric("Sorting colour", sorting_color(snap, history_df)[0])
    else:
        st.markdown('<div class="section-title">Operational summary</div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        c1.metric("Current stage", process_state.phase)
        c2.metric("Active regions", f"{sum(r.active for r in region_statuses(snap))}/5")
        c3.metric("Data source", snap.source.value)

    st.markdown('<div class="identity-footer">Visual identity only in this version. Analytical charts and plots are intentionally deferred.</div>', unsafe_allow_html=True)


# ------------------------------ main ---------------------------------------
inject_visual_identity()

with st.sidebar:
    st.header("Dashboard")
    view = st.radio("Audience", ["Operating Manager", "Line Manager", "General Management"], index=0)
    st.divider()
    st.header("Data source")
    modes = ["Auto", SourceMode.LIVE.value, SourceMode.SIMULATION.value, SourceMode.REPLAY.value]
    mode = st.radio("Mode", modes, index=0)
    reset_history_if_source_changed(mode)
    st.caption(f"OPC UA: `{SETTINGS.opcua_url}` · namespace `{SETTINGS.opcua_namespace}`")
    refresh = st.slider("Dashboard refresh (ms)", 500, 5000, SETTINGS.refresh_ms, 250)

    sessions = session_dirs()
    selected_session = None
    if view in {"Line Manager", "General Management"} and sessions:
        labels = [session_label(p) for p in sessions[:20]]
        selected_label = st.selectbox("Recorded session", ["Current live window"] + labels)
        if selected_label != "Current live window":
            selected_session = sessions[labels.index(selected_label)]
    if mode == SourceMode.SIMULATION.value:
        sim = get_sim()
        c1, c2 = st.columns(2)
        if c1.button("Pause / Run", use_container_width=True):
            sim.toggle(); st.rerun()
        if c2.button("Reset", use_container_width=True):
            sim.reset(); st.rerun()
    elif mode == SourceMode.REPLAY.value:
        st.subheader("Replay navigator")
        if not sessions:
            st.warning("No recorded sessions found.")
        else:
            labels = [session_label(p) for p in sessions[:50]]
            current_path = st.session_state.get("replay_selected_path")
            default_idx = next((i for i, p in enumerate(sessions[:50]) if str(p / "telemetry.csv") == current_path), 0)
            replay_label = st.selectbox("Recording session", labels, index=default_idx, key="replay_session_select")
            replay_path = sessions[labels.index(replay_label)] / "telemetry.csv"
            if current_path != str(replay_path):
                st.session_state.replay_selected_path = str(replay_path)
                st.session_state.replay_playing = False
                st.session_state.replay_speed = 1
                st.session_state.replay_source = ReplayDataSource(replay_path)
                # A replay session is an independent timeline. Never mix its
                # history/events with the previous live or replay session.
                st.session_state.replay_history = History(max_rows=max(1200, SETTINGS.history_seconds * 4))
                st.session_state.event_engine = EventEngine(
                    SETTINGS.process_watchdog_seconds, SETTINGS.opcua_sync_warning_ms
                )
                st.session_state.events = []
                st.session_state.replay_focus_incident = None

            replay = get_replay(replay_path)
            incidents = incident_records(replay_path.parent)
            if incidents:
                incident_labels = [
                    f"{r['timestamp'].strftime('%H:%M:%S UTC') if not pd.isna(r['timestamp']) else 'unknown'} · {r['severity']} · {r['code']} · {r['message']}"
                    for r in incidents
                ]
                incident_choice = st.selectbox("Recorded incident", ["No incident / choose timestamp"] + incident_labels, key="replay_incident_select")
                if incident_choice != "No incident / choose timestamp":
                    chosen = incidents[incident_labels.index(incident_choice)]
                    if st.button("▶ Replay this incident", use_container_width=True):
                        if not pd.isna(chosen["timestamp"]):
                            pre_seconds = 60.0
                            meta_path = chosen["path"] / "metadata.json"
                            if meta_path.exists():
                                try:
                                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                                    pre_seconds = float(meta.get("pre_seconds", pre_seconds))
                                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                                    pass
                            event_time = chosen["timestamp"].to_pydatetime()
                            seek_replay_to_timestamp(replay_path, event_time - pd.Timedelta(seconds=pre_seconds))
                            st.session_state.replay_focus_incident = chosen["id"]
                            st.session_state.replay_playing = True
                            st.session_state.replay_history = History(max_rows=max(1200, SETTINGS.history_seconds * 4))
                            st.session_state.event_engine = EventEngine(
                                SETTINGS.process_watchdog_seconds, SETTINGS.opcua_sync_warning_ms
                            )
                            st.session_state.events = []
                            st.rerun()
            else:
                st.caption("No recorded incidents in this session.")

            start_dt = replay.start_timestamp
            end_dt = replay.end_timestamp
            if start_dt and end_dt:
                default_date = start_dt.date()
                target_date = st.date_input("Replay date (UTC)", value=default_date, key="replay_date")
                target_time = st.time_input("Jump to time (UTC)", value=start_dt.time().replace(microsecond=0), key="replay_time")
                if st.button("Jump to timestamp", use_container_width=True):
                    target = datetime.combine(target_date, target_time, tzinfo=timezone.utc)
                    seek_replay_to_timestamp(replay_path, target)
                    st.rerun()

                st.caption(f"Available: {start_dt.strftime('%Y-%m-%d %H:%M:%S')} → {end_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")

            c1, c2 = st.columns(2)
            if c1.button("⏮ Start", use_container_width=True):
                replay.reset(); st.session_state.replay_playing = False; st.rerun()
            if c2.button("↺ Restart", use_container_width=True):
                replay.reset(); st.session_state.replay_playing = False; st.rerun()

            speeds = [1, 2, 5, 10, 25]
            speed = st.select_slider("Replay speed", options=speeds, value=st.session_state.get("replay_speed", 1), format_func=lambda x: f"{x}×", key="replay_speed")
            if st.button("⏯ Play / Pause", use_container_width=True):
                st.session_state.replay_playing = not st.session_state.get("replay_playing", False)
                st.rerun()
            if replay.current_timestamp:
                st.caption(f"Position: {replay.current_timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC · row {min(replay.position, replay.total_rows)}/{replay.total_rows}")

# Replay navigation is independent from live acquisition. The OPC UA reader
# keeps polling in its own worker regardless of the selected dashboard source.
st_autorefresh(interval=refresh, key="dashboard_refresh")

# ---- Background live pipeline ------------------------------------------------
# Always drain the live reader buffer. This is intentionally independent of the
# currently displayed source, so reviewing a recording never stops acquisition,
# recording, incident detection, or live state capture.
reader = get_reader()
recorder = get_recorder()
live_engine = get_live_event_engine()
last_processed_id = st.session_state.get("last_processed_live_id")
unread = reader.snapshots_since(last_processed_id)
if unread:
    if not recorder.active:
        recorder.start()
    live_history = get_live_history()
    live_events = st.session_state.setdefault("live_events", [])
    for item in unread:
        live_history.add(item)
        recorder.record(item)
        new_events = live_engine.evaluate(item)
        live_events.extend(new_events)
        del live_events[:-500]
        recorder.record_process_state(live_engine.process_state)
        for event in new_events:
            recorder.record_event(event)
        if item.snapshot_id is not None:
            st.session_state.last_processed_live_id = item.snapshot_id

# ---- Display pipeline --------------------------------------------------------
# This pipeline is disposable: changing views or switching between replay/live
# never touches the background live pipeline above.
if mode == SourceMode.REPLAY.value:
    replay = replay_source()
    if replay is None:
        snap = get_sim().snapshot()
    else:
        if st.session_state.get("replay_playing"):
            batch = replay.snapshots_batch(st.session_state.get("replay_speed", 1))
        else:
            batch = [replay.snapshot()]
        replay_engine = get_event_engine()
        replay_history = get_replay_history()
        replay_events = st.session_state.setdefault("events", [])
        for item in batch:
            replay_history.add(item)
            replay_events.extend(replay_engine.evaluate(item))
        del replay_events[:-500]
        snap = batch[-1]
elif mode == SourceMode.SIMULATION.value:
    snap = get_sim().snapshot()
    history = get_history()
    history.add(snap)
    events_now = get_event_engine().evaluate(snap)
    st.session_state.setdefault("events", []).extend(events_now)
    st.session_state.events = st.session_state.events[-500:]
else:
    # Auto and Live both display the latest live state. The background pipeline
    # above has already persisted every unread live snapshot.
    snap = reader.snapshot()
    st.session_state.history = get_live_history()
    st.session_state.event_engine = live_engine
    st.session_state.events = st.session_state.get("live_events", [])

event_engine = get_event_engine()
process_state = event_engine.process_state

if mode == SourceMode.REPLAY.value:
    replay = replay_source()
    if replay is not None:
        focused = st.session_state.get("replay_focus_incident")
        if focused:
            focus_rows = [r for r in incident_records(Path(st.session_state["replay_selected_path"]).parent) if r["id"] == focused]
            if focus_rows and not pd.isna(focus_rows[0]["timestamp"]):
                focus = focus_rows[0]
                current_ts = pd.Timestamp(snap.source_timestamp_reference or snap.timestamp)
                event_ts = focus["timestamp"]
                delta = (current_ts - event_ts).total_seconds()
                st.info(
                    f"🎬 Replay incident **{focus['code']}** · {focus['severity']} · "
                    f"{focus['message']} · event {event_ts.strftime('%H:%M:%S UTC')} · "
                    f"position {delta:+.1f}s from event"
                )
status_label = "EMERGENCY" if snap.emergency else "STALE" if snap.health == Health.STALE else "CONNECTED" if snap.connected else "OFFLINE"
phase = process_state.phase if snap.source != SourceMode.SIMULATION else process_phase(snap)
cols = st.columns(5)
cols[0].metric("Connection", status_label)
cols[1].metric("Current stage", phase)
cols[2].metric("Burning", "ON" if snap.bool("ms.process.burn") else "OFF")
cols[3].metric("Stage elapsed", fmt_seconds(process_state.phase_elapsed_s))
cols[4].metric("Cycle", process_state.cycle if process_state.cycle else "—")

if mode == SourceMode.REPLAY.value:
    history_df = get_replay_history().dataframe()
    analysis_session = (Path(st.session_state["replay_selected_path"]).parent
                        if st.session_state.get("replay_selected_path") else None)
elif mode == SourceMode.SIMULATION.value:
    history_df = get_history().dataframe()
    analysis_session = selected_session if selected_session is not None else None
else:
    history_df = get_live_history().dataframe()
    analysis_session = selected_session if selected_session is not None else None
clean, cycles, stages, events_df = analysis_data(snap, history_df, analysis_session)

# During replay, make the recorded event log visible instead of hiding it behind
# the newly reconstructed event engine. The engine still runs to reconstruct
# process state and region activity frame-by-frame.
if mode == SourceMode.REPLAY.value and analysis_session is not None:
    recorded_events = load_events(analysis_session)
    if not recorded_events.empty:
        events_df = recorded_events

render_role_visual(view, snap, process_state, clean, cycles, stages, events_df)

with st.expander("Current factory diagnostics", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.write("**Emergency source:**", emergency_source(snap))
        color_value = snap.get("sl.sensor.color_value")
        color_name, color_source = sorting_color(snap, history_df)
        st.write("**Color:**", f"{color_name} · {color_source} (raw value {color_value})")
        if color_name == "Unknown" and color_source == "unclassified sensor value":
            st.caption("The raw color value is not in the calibrated PLC range; it is not assumed to be Blue.")
        coords = (snap.get("local.crane_coord_h"), snap.get("local.crane_coord_v"), snap.get("local.crane_coord_r"))
        if all(v is not None for v in coords):
            st.write("**Crane H / V / R:**", f"{coords[0]} / {coords[1]} / {coords[2]}")
    with c2:
        st.write(f"**Values received:** {len(snap.values)}")
        st.write(f"**Good / Bad:** {snap.good_count} / {snap.bad_count}")
        st.write(f"**Source:** {snap.message}")

with st.expander("Raw variable table", expanded=False):
    descriptions = tag_descriptions()
    current = pd.DataFrame([
        {"Tag": key, "Value": value, "Status": snap.statuses.get(key, "derived"),
         "Description": descriptions.get(key, "Simulation / derived value")}
        for key, value in sorted(snap.values.items())
    ])
    st.dataframe(current, use_container_width=True, hide_index=True)

if recorder.active:
    st.caption(f"Recording live session: `{recorder.session_dir.name}` · raw telemetry + cleaned analytics + events")
else:
    st.caption("Safety: read-only OPC UA access. No write nodes, actuator control, emergency reset, or PLC commands are implemented.")
