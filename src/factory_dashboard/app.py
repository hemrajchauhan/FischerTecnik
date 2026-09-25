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

from .analytics import (
    OVEN_TARGET_SECONDS,
    attach_source_timestamps,
    build_color_counts,
    build_cycle_metrics,
    build_stage_metrics,
    clean_telemetry,
    kpi_summary,
)
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
    complaints_figure,
    cake_flavour_figure,
    case_study_complaints,
    cycle_time_figure,
    throughput_figure,
    event_timeline_figure,
    factory_figure,
    oee_components_figure,
    qa_burn_figure,
    simulated_temperature_figure,
    stage_duration_figure,
    station_activity_figure,
)

st.set_page_config(page_title="Smart Cake Factory", page_icon="🍰", layout="wide")


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


def seed_simulation_history() -> None:
    """Populate a short deterministic history so simulation charts are useful immediately."""
    if st.session_state.get("simulation_history_seeded"):
        return
    history = get_history()
    sim = get_sim()
    now = datetime.now(timezone.utc)
    total = 330.0
    start = now - pd.Timedelta(seconds=total)
    for i in range(int(total) + 1):
        elapsed = float(i)
        ts = start + pd.Timedelta(seconds=elapsed)
        history.add(sim.snapshot_at_elapsed(elapsed, timestamp=ts))
    st.session_state.simulation_history_seeded = True


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
    """Cake-factory visual system based on DASHBOARD_SPEC_1.md and the supplied references."""
    st.markdown(
        """
        <style>
        :root { --tum-blue:#0065BD; --light-blue:#98C6EA; --orange:#F28C28; --orange-dark:#B34700; --green:#2E8B57; --grey:#B0B0B5; --ink:#1D1D1F; --muted:#595960; --grid:#E5E5EA; }
        html, body, [class*="css"] { font-family: Arial, sans-serif; }
        .block-container { padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1500px; }
        [data-testid="stSidebar"] { background:#F3F5F8; border-right:1px solid var(--grid); }
        [data-testid="stSidebar"] .block-container { padding-top:1.1rem; }
        .identity-eyebrow { color:var(--tum-blue); font-size:.78rem; font-weight:800; letter-spacing:.14em; text-transform:uppercase; margin-bottom:.25rem; }
        .identity-title { color:var(--ink); font-size:2rem; line-height:1.08; font-weight:750; margin:0; }
        .identity-subtitle { color:var(--muted); font-size:.96rem; margin-top:.42rem; margin-bottom:1rem; }
        .source-pill { background:#F6F7F9; color:var(--ink); border:1px solid var(--grid); border-radius:999px; padding:.45rem .78rem; font-size:.76rem; font-weight:700; white-space:nowrap; }
        .status-banner { border-radius:20px; padding:1rem 1.2rem; margin:.15rem 0 1rem; display:flex; align-items:center; justify-content:space-between; gap:1rem; border:1px solid transparent; }
        .status-banner .state { font-size:1.3rem; font-weight:850; letter-spacing:.02em; }
        .status-banner .meta { color:var(--muted); font-size:.88rem; text-align:right; }
        .status-live { background:#EAF3FB; border-color:#C9E2F5; } .status-live .state { color:var(--tum-blue); }
        .status-stop,.status-alert { background:#FFF2E8; border-color:#F7D1B2; } .status-stop .state,.status-alert .state { color:var(--orange-dark); }
        .status-idle { background:#F2F2F4; border-color:#DEDEE2; } .status-idle .state { color:var(--muted); }
        .status-replay { background:#EEF6FD; border-color:#C9E2F5; } .status-replay .state { color:var(--tum-blue); }
        .kpi-card { background:#fff; border:1px solid var(--grid); border-radius:18px; padding:.95rem 1rem .82rem; min-height:112px; box-shadow:0 5px 18px rgba(29,31,33,.055); }
        .kpi-label { color:var(--muted); font-size:.68rem; font-weight:850; letter-spacing:.12em; text-transform:uppercase; margin-bottom:.38rem; }
        .kpi-value { color:var(--ink); font-size:2.0rem; line-height:1.05; font-weight:750; }
        .kpi-value.attention { color:var(--orange-dark); } .kpi-sub { color:var(--muted); font-size:.78rem; margin-top:.42rem; }
        .section-title { color:var(--ink); font-size:1.22rem; font-weight:750; margin:.5rem 0 .22rem; }
        .chart-headline { color:var(--ink); font-size:1.35rem; line-height:1.14; font-weight:750; margin:.35rem 0 .18rem; }
        .section-note { color:var(--muted); font-size:.84rem; margin-bottom:.45rem; }
        .factory-frame { background:#fff; border:1px solid var(--grid); border-radius:20px; padding:.4rem; box-shadow:0 5px 18px rgba(29,31,33,.055); }
        .station-card { background:#fff; border:1px solid var(--grid); border-radius:15px; padding:.72rem .82rem; margin-bottom:.48rem; box-shadow:0 3px 12px rgba(29,31,33,.04); }
        .station-top { display:flex; justify-content:space-between; align-items:center; gap:.5rem; }
        .station-name { color:var(--ink); font-weight:750; font-size:.88rem; }
        .station-state { font-size:.68rem; font-weight:850; letter-spacing:.06em; }
        .station-detail { color:var(--muted); font-size:.73rem; margin-top:.3rem; line-height:1.35; }
        .dot-active { color:var(--tum-blue); } .dot-idle { color:var(--grey); } .dot-alert { color:var(--orange-dark); }
        .alert-card { border-left:4px solid var(--orange); background:#FFF7F0; border-radius:12px; padding:.72rem .85rem; margin:.45rem 0; }
        .alert-title { color:var(--orange-dark); font-weight:800; font-size:.84rem; }
        .alert-detail { color:var(--muted); font-size:.77rem; margin-top:.15rem; } .info-card { border-left:4px solid var(--tum-blue); background:#EEF6FD; border-radius:12px; padding:.72rem .85rem; margin:.45rem 0; }
        .identity-footer { color:#7A7A80; font-size:.72rem; margin-top:.9rem; }
        div[data-testid="stMetric"] { background:#fff; border:1px solid var(--grid); border-radius:16px; padding:.65rem .75rem; box-shadow:0 4px 14px rgba(29,31,33,.04); }
        div[data-testid="stDataFrame"] { border-radius:14px; overflow:hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def role_title(view: str) -> tuple[str, str]:
    return {
        "Line Operator": ("Line Operator", "Cake line cockpit"),
        "QA Manager": ("QA Manager", "Quality control room"),
        "Department Manager": ("Department Manager", "Cake production overview"),
        "Factory Diagnostics": ("Factory Diagnostics", "Virtualisation & signal diagnostics"),
    }[view]


def source_pill(snap: Snapshot) -> str:
    label = "LIVE" if snap.source == SourceMode.LIVE else f"REPLAY {st.session_state.get('replay_speed', 1)}×" if snap.source == SourceMode.REPLAY else "SIMULATION"
    stamp = snap.source_timestamp_reference or snap.timestamp
    ts = pd.to_datetime(stamp, utc=True, errors="coerce")
    time_text = ts.tz_convert("Europe/Berlin").strftime("%d %b %Y %H:%M:%S") if not pd.isna(ts) else "—"
    return f"{label} · {time_text}"


def render_identity_header(view: str, snap: Snapshot) -> None:
    role, title = role_title(view)
    st.markdown(
        f'''<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;">
        <div><div class="identity-eyebrow">SMART CAKE FACTORY · {role.upper()}</div>
        <div class="identity-title">{title}</div>
        <div class="identity-subtitle">Fischertechnik production data presented as a cake manufacturing line.</div></div>
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
        css, state = "status-live", "CAKE LINE RUNNING"
    elapsed_s = getattr(process_state, "phase_elapsed_s", None)
    elapsed = fmt_seconds(elapsed_s)
    if str(process_state.phase).strip().lower() == "baking":
        detail = f"Oven elapsed: {elapsed} · reference {OVEN_TARGET_SECONDS:.1f} s"
    else:
        detail = f"Stage: {process_state.phase} · Stage elapsed: {elapsed}"
    st.markdown(f'''<div class="status-banner {css}"><div class="state">{state}</div><div class="meta">{detail}</div></div>''', unsafe_allow_html=True)


def identity_kpi(label: str, value: str, sub: str = "", attention: bool = False) -> str:
    cls = "kpi-value attention" if attention else "kpi-value"
    return f'''<div class="kpi-card"><div class="kpi-label">{label}</div><div class="{cls}">{value}</div><div class="kpi-sub">{sub}</div></div>'''


def render_kpis(cards: list[str]) -> None:
    cols = st.columns(len(cards))
    for col, card in zip(cols, cards):
        with col:
            st.markdown(card, unsafe_allow_html=True)


def _oee_values(summary: dict) -> tuple[float, float, float, float]:
    elapsed = max(float(summary.get("duration_s") or 1.0), 1.0)
    downtime = float(summary.get("stop_time_s") or 0.0)
    availability = max(0.0, min(1.0, 1.0 - downtime / elapsed))
    pickup = pd.Series(summary.get("pickup_intervals_s") or [], dtype=float).dropna()
    nonslow = pickup[pickup <= 55.0 * 1.5]
    median_pickup = float(nonslow.median()) if not nonslow.empty else 55.0
    performance = max(0.0, min(1.0, 55.0 / median_pickup))
    quality = float(summary.get("fpy")) if summary.get("fpy") is not None else 1.0
    oee = availability * performance * quality
    return availability, performance, quality, oee


def render_factory_identity(snap: Snapshot, *, compact: bool = False) -> None:
    regions = region_statuses(snap)
    st.markdown('<div class="section-title">Cake factory virtualisation</div><div class="section-note">Highlighted areas are driven by physical process outputs. Sensor/reference states are kept separate from activity.</div>', unsafe_allow_html=True)
    left, right = st.columns([1.85, 1] if not compact else [1.55, 1])
    with left:
        st.markdown('<div class="factory-frame">', unsafe_allow_html=True)
        st.plotly_chart(factory_figure(ASSETS / "factory_overview.jpg", regions), use_container_width=True, config={"displayModeBar": False})
        st.markdown('</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="section-title">Cake-line stations</div>', unsafe_allow_html=True)
        cake_labels = {
            "Ingredient & Tray Storage": "Ingredient & tray storage",
            "Cake Handling Crane": "Cake handling",
            "Baking Oven & Processing": "Baking oven & processing",
            "Decoration / Finishing": "Decoration / finishing",
            "Quality Inspection & Dispatch": "Quality inspection & dispatch",
        }
        for region in regions:
            if region.fault:
                dot, state, cls = "●", "ATTENTION", "dot-alert"
            elif region.active:
                dot, state, cls = "●", "ACTIVE", "dot-active"
            else:
                dot, state, cls = "●", "IDLE", "dot-idle"
            label = cake_labels.get(region.label, region.label)
            st.markdown(f'''<div class="station-card"><div class="station-top"><div class="station-name">{label}</div><div class="station-state {cls}">{dot} {state}</div></div><div class="station-detail">{region.detail}</div></div>''', unsafe_allow_html=True)


def _recent_alerts(events: pd.DataFrame) -> pd.DataFrame:
    if events is None or events.empty:
        return pd.DataFrame()
    out = events.copy()
    if "timestamp" in out.columns:
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    if "severity" in out.columns:
        out = out[out["severity"].isin(["WARNING", "CRITICAL"])].copy()
    return out.sort_values("timestamp") if "timestamp" in out.columns else out


def render_operator(view: str, snap: Snapshot, process_state, clean: pd.DataFrame, cycles: pd.DataFrame, stages: pd.DataFrame, events: pd.DataFrame) -> None:
    summary = kpi_summary(clean, cycles, stages, events)
    colors = build_color_counts(clean)
    render_identity_header(view, snap)
    render_status_banner(snap, process_state)
    render_kpis([
        identity_kpi("Cakes completed", str(summary.get("cycles_completed", 0)), "HBW pickup → sorting-line entry"),
        identity_kpi("Cycle time", fmt_seconds(summary.get("cycle_median_s")), "Actual material-flow cycle"),
        identity_kpi("Oven time", fmt_seconds(summary.get("burn_median_s")), f"Actual burn time · reference {OVEN_TARGET_SECONDS:.1f} s"),
        identity_kpi("Vanilla", str(colors["Vanilla"]), "White cakes produced"),
        identity_kpi("Strawberry", str(colors["Strawberry"]), "Red cakes produced"),
        identity_kpi("Blueberry", str(colors["Blueberry"]), "Blueberry cakes produced"),
    ])

    render_factory_identity(snap, compact=False)

    if snap.emergency:
        st.error("EMERGENCY STOP ACTIVE — follow the physical plant safety procedure.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="chart-headline">Cake cycle timing</div><div class="section-note">Actual HBW pickup → sorting-line entry duration · 55 s reference shown for context.</div>', unsafe_allow_html=True)
        st.plotly_chart(cycle_time_figure(cycles), use_container_width=True, config={"displayModeBar": False})
    with c2:
        st.markdown('<div class="chart-headline">Oven temperature</div><div class="section-note">Simulated process temperature for the cake-factory presentation · target window 175–185 °C.</div>', unsafe_allow_html=True)
        st.plotly_chart(simulated_temperature_figure(clean), use_container_width=True, config={"displayModeBar": False})



def render_qa(view: str, snap: Snapshot, clean: pd.DataFrame, cycles: pd.DataFrame, stages: pd.DataFrame, events: pd.DataFrame) -> None:
    summary = kpi_summary(clean, cycles, stages, events)
    colors = build_color_counts(clean)
    checks = int(summary.get("quality_checks", 0) or 0)
    passes = int(summary.get("quality_passes", 0) or 0)
    nok = max(checks - passes, 0)
    render_identity_header(view, snap)
    render_kpis([
        identity_kpi("Vanilla cakes", str(colors["Vanilla"]), "White cakes produced"),
        identity_kpi("Strawberry cakes", str(colors["Strawberry"]), "Red cakes produced"),
        identity_kpi("Blueberry cakes", str(colors["Blueberry"]), "Blue cakes produced"),
        identity_kpi("NOK cakes", str(nok), "Oven time outside 3–7 s tolerance", attention=nok > 0),
    ])

    left, right = st.columns([2, 3])
    with left:
        st.markdown('<div class="chart-headline">Cakes produced by flavour</div><div class="section-note">Finished cakes classified by the sorting-line destination: white = vanilla, red = strawberry, blue = blueberry.</div>', unsafe_allow_html=True)
        st.plotly_chart(cake_flavour_figure(colors), use_container_width=True, config={"displayModeBar": False})
    with right:
        st.markdown('<div class="chart-headline">Latest cakes checked</div><div class="section-note">Newest finished cakes · actual oven time measured from burn ON → OFF.</div>', unsafe_allow_html=True)
        burn_rows = stages[(stages.get("measurement") == "stage_elapsed") & (stages.get("stage") == "Baking")].copy() if not stages.empty else pd.DataFrame()
        if not burn_rows.empty:
            burn_rows = burn_rows.dropna(subset=["elapsed_s"]).sort_values("start", ascending=False).head(8).copy()
            burn_rows["Time"] = pd.to_datetime(burn_rows["start"], utc=True, errors="coerce").dt.tz_convert("Europe/Berlin").dt.strftime("%H:%M:%S")
            burn_rows["Cake"] = [f"CK-{int(x):04d}" for x in burn_rows["occurrence"]]
            burn_rows["Bake (s)"] = burn_rows["elapsed_s"].round(1)
            burn_rows["Result"] = burn_rows["elapsed_s"].apply(lambda x: "OK" if 3 <= x <= 7 else "NOK")
            st.dataframe(burn_rows[["Time", "Cake", "Bake (s)", "Result"]], use_container_width=True, hide_index=True)
        else:
            st.info("No completed cake quality checks in this window.")

    headline = f"{nok} cake{'s' if nok != 1 else ''} need quality review" if nok else f"All {checks} checked cakes stayed within the bake-time specification"
    st.markdown(f'<div class="chart-headline">{headline}</div><div class="section-note">Actual oven time per cake · target 5 s · acceptable tolerance 3–7 s.</div>', unsafe_allow_html=True)
    st.plotly_chart(qa_burn_figure(stages), use_container_width=True, config={"displayModeBar": False})


def render_department(view: str, snap: Snapshot, clean: pd.DataFrame, cycles: pd.DataFrame, stages: pd.DataFrame, events: pd.DataFrame) -> None:
    summary = kpi_summary(clean, cycles, stages, events)
    availability, performance, quality, oee = _oee_values(summary)
    complaints = case_study_complaints()
    latest_month = list(complaints)[-1]
    latest_complaints = complaints[latest_month]
    render_identity_header(view, snap)
    render_kpis([
        identity_kpi("OEE", f"{oee*100:.0f} %", "Availability × performance × quality"),
        identity_kpi("Availability", f"{availability*100:.0f} %", f"{summary.get('unplanned_stops', 0)} unplanned stops · {fmt_seconds(summary.get('stop_time_s'))}"),
        identity_kpi("Quality", f"{quality*100:.0f} %", f"{summary.get('quality_passes', 0)} OK / {summary.get('quality_checks', 0)} cakes"),
        identity_kpi(f"Complaints · {latest_month}", str(latest_complaints), "Manual case-study input"),
    ])

    left, right = st.columns(2)
    with left:
        st.markdown(f'<div class="chart-headline">{latest_month} complaints: {latest_complaints}</div><div class="section-note">Consumer complaints per month · manual case-study input.</div>', unsafe_allow_html=True)
        st.plotly_chart(complaints_figure(), use_container_width=True, config={"displayModeBar": False})
    with right:
        weak = "Availability" if availability < .95 else "Performance" if performance < .95 else "Quality" if quality < .95 else None
        if weak:
            headline = f"OEE is {oee*100:.0f}% with {weak.lower()} below target"
        else:
            headline = f"OEE is {oee*100:.0f}% with no component loss"
        st.markdown(f'<div class="chart-headline">{headline}</div><div class="section-note">Availability, performance and quality components · losses below 95% are highlighted.</div>', unsafe_allow_html=True)
        st.plotly_chart(oee_components_figure(availability, performance, quality), use_container_width=True, config={"displayModeBar": False})


def render_diagnostics(view: str, snap: Snapshot, process_state, clean: pd.DataFrame, cycles: pd.DataFrame, stages: pd.DataFrame, events: pd.DataFrame) -> None:
    summary = kpi_summary(clean, cycles, stages, events)
    colors = build_color_counts(clean)
    render_identity_header(view, snap)
    render_kpis([
        identity_kpi("Factory state", "EMERGENCY" if snap.emergency else ("STALE" if snap.health == Health.STALE else "RUNNING" if snap.connected else "OFFLINE"),
                     "Current telemetry state", attention=snap.emergency or snap.health == Health.STALE or not snap.connected),
        identity_kpi("Signals", str(len(snap.values)), f"{snap.good_count} good · {snap.bad_count} bad"),
        identity_kpi("Sync spread", f"{snap.sync_spread_ms:.0f} ms" if snap.sync_spread_ms is not None else "—", "SourceTimestamp spread"),
        identity_kpi("Current stage", process_state.phase, f"Elapsed {fmt_seconds(process_state.phase_elapsed_s)}"),
        identity_kpi("Oven time", fmt_seconds(summary.get("burn_median_s")), f"Actual median · reference {OVEN_TARGET_SECONDS:.1f} s"),
        identity_kpi("Cakes", str(summary.get("cycles_completed", 0)), "Completed material-flow cycles"),
        identity_kpi("Throughput", f'{summary.get("throughput_per_hour"):.1f} cakes/h' if summary.get("throughput_per_hour") is not None else "—", "Estimated from HBW pickup cadence"),
    ])

    render_factory_identity(snap, compact=False)

    st.markdown('<div class="section-title">What needs attention</div>', unsafe_allow_html=True)
    if events is None or events.empty:
        st.info("No recorded factory events in the current window.")
    else:
        e = events.copy()
        if "timestamp" in e.columns:
            e["timestamp"] = pd.to_datetime(e["timestamp"], utc=True, errors="coerce")
        severity_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
        e["_rank"] = e.get("severity", pd.Series("INFO", index=e.index)).map(severity_order).fillna(2)
        e = e.sort_values(["_rank", "timestamp"], ascending=[True, False])
        for _, row in e.head(12).iterrows():
            sev = str(row.get("severity", "INFO"))
            ts = row.get("timestamp")
            ts_text = ts.tz_convert("Europe/Berlin").strftime("%H:%M:%S") if pd.notna(ts) else "—"
            card_cls = "alert-card" if sev in {"CRITICAL", "WARNING"} else "info-card"
            html = '<div class="' + card_cls + '"><div class="alert-title">' +                    f'{sev} · {row.get("code", "EVENT")} · {ts_text}' +                    '</div><div class="alert-detail">' + str(row.get("message", "Recorded event")) + '</div></div>'
            st.markdown(html, unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="chart-headline">Cake cycle timing</div><div class="section-note">Actual HBW pickup → sorting-line entry duration.</div>', unsafe_allow_html=True)
        st.plotly_chart(cycle_time_figure(cycles), use_container_width=True, config={"displayModeBar": False})
    with c2:
        st.markdown('<div class="chart-headline">Production throughput</div><div class="section-note">HBW pickup-to-pickup cadence used to estimate cakes per hour.</div>', unsafe_allow_html=True)
        st.plotly_chart(throughput_figure(cycles), use_container_width=True, config={"displayModeBar": False})

    c3, c4 = st.columns(2)
    with c3:
        st.markdown('<div class="section-title">Observed stage durations</div><div class="section-note">Measured from signal transitions and source timestamps.</div>', unsafe_allow_html=True)
        st.plotly_chart(stage_duration_figure(stages), use_container_width=True, config={"displayModeBar": False})
    with c4:
        st.markdown('<div class="section-title">Station activity</div><div class="section-note">Share of observed time with physical process outputs active.</div>', unsafe_allow_html=True)
        st.plotly_chart(station_activity_figure(clean), use_container_width=True, config={"displayModeBar": False})

    c5, c6 = st.columns(2)
    with c5:
        st.markdown('<div class="section-title">Cakes by flavour</div><div class="section-note">White = vanilla · red = strawberry · blue = blueberry.</div>', unsafe_allow_html=True)
        st.plotly_chart(cake_flavour_figure(colors), use_container_width=True, config={"displayModeBar": False})
    with c6:
        st.markdown('<div class="section-title">Event timeline</div><div class="section-note">Recorded INFO, WARNING and CRITICAL events.</div>', unsafe_allow_html=True)
        if events is not None and not events.empty:
            st.plotly_chart(event_timeline_figure(events), use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("No events in the current window.")

    st.markdown('<div class="section-title">Factory signal diagnostics</div><div class="section-note">Raw sensor, actuator, PLC-state and derived values. TRUE sensors are not automatically treated as machine activity.</div>', unsafe_allow_html=True)
    descriptions = tag_descriptions()
    current = pd.DataFrame([
        {"Signal": key, "Value": value, "Status": snap.statuses.get(key, "derived"),
         "Description": descriptions.get(key, "Simulation / derived value")}
        for key, value in sorted(snap.values.items())
    ])
    st.dataframe(current, use_container_width=True, hide_index=True, height=480)

    st.markdown('<div class="section-title">Process summary</div>', unsafe_allow_html=True)
    process_rows = [
        ("Completed cycles", summary.get("cycles_completed", 0)),
        ("Median cycle time", fmt_seconds(summary.get("cycle_median_s"))),
        ("Throughput", f'{summary.get("throughput_per_hour"):.1f} cakes/h' if summary.get("throughput_per_hour") is not None else "—"),
        ("Median oven time", fmt_seconds(summary.get("burn_median_s"))),
        ("Oven reference", f"{OVEN_TARGET_SECONDS:.1f} s"),
        ("Vanilla / Strawberry / Blueberry", f'{colors["Vanilla"]} / {colors["Strawberry"]} / {colors["Blueberry"]}'),
        ("Bottleneck stage", summary.get("bottleneck_stage") or "—"),
        ("Bottleneck median", fmt_seconds(summary.get("bottleneck_median_s"))),
        ("Telemetry samples", summary.get("samples", 0)),
    ]
    st.dataframe(pd.DataFrame(process_rows, columns=["Metric", "Value"]), use_container_width=True, hide_index=True)



def render_role_visual(view: str, snap, process_state, clean, cycles, stages, events):
    if view == "Line Operator":
        render_operator(view, snap, process_state, clean, cycles, stages, events)
    elif view == "QA Manager":
        render_qa(view, snap, clean, cycles, stages, events)
    elif view == "Department Manager":
        render_department(view, snap, clean, cycles, stages, events)
    else:
        render_diagnostics(view, snap, process_state, clean, cycles, stages, events)


# ------------------------------ main ---------------------------------------
inject_visual_identity()

with st.sidebar:
    st.markdown("## Smart Cake Factory")
    view = st.radio("View as", ["Line Operator", "QA Manager", "Department Manager", "Factory Diagnostics"], index=0)
    st.divider()
    st.markdown("### Data source")
    modes = ["Auto", SourceMode.LIVE.value, SourceMode.SIMULATION.value, SourceMode.REPLAY.value]
    mode = st.radio("Mode", modes, index=0)
    reset_history_if_source_changed(mode)
    st.caption(f"OPC UA: `{SETTINGS.opcua_url}`")
    refresh = st.slider("Refresh (ms)", 500, 5000, SETTINGS.refresh_ms, 250)

    sessions = session_dirs()
    selected_session = None
    if view in {"Department Manager", "Factory Diagnostics"} and sessions and mode != SourceMode.REPLAY.value:
        labels = [session_label(p) for p in sessions[:20]]
        selected_label = st.selectbox("Recorded session", ["Current live window"] + labels)
        if selected_label != "Current live window":
            selected_session = sessions[labels.index(selected_label)]

    if mode == SourceMode.SIMULATION.value:
        sim = get_sim()
        seed_simulation_history()
        c1, c2 = st.columns(2)
        if c1.button("Pause / Run", use_container_width=True):
            sim.toggle(); st.rerun()
        if c2.button("Reset", use_container_width=True):
            sim.reset()
            st.session_state.history = History(max_rows=max(1200, SETTINGS.history_seconds * 4))
            st.session_state.events = []
            st.session_state.simulation_history_seeded = False
            seed_simulation_history()
            st.rerun()
    elif mode == SourceMode.REPLAY.value:
        st.markdown("### Replay")
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
                st.session_state.replay_history = History(max_rows=max(1200, SETTINGS.history_seconds * 4))
                st.session_state.event_engine = EventEngine(SETTINGS.process_watchdog_seconds, SETTINGS.opcua_sync_warning_ms)
                st.session_state.events = []
                st.session_state.replay_focus_incident = None

            replay = get_replay(replay_path)
            incidents = incident_records(replay_path.parent)
            if incidents:
                incident_labels = [
                    f"{r['timestamp'].strftime('%H:%M:%S UTC') if not pd.isna(r['timestamp']) else 'unknown'} · {r['severity']} · {r['code']}"
                    for r in incidents
                ]
                incident_choice = st.selectbox("Recorded incident", ["No incident / choose timestamp"] + incident_labels, key="replay_incident_select")
                if incident_choice != "No incident / choose timestamp":
                    chosen = incidents[incident_labels.index(incident_choice)]
                    if st.button("▶ Replay this incident", use_container_width=True) and not pd.isna(chosen["timestamp"]):
                        pre_seconds = 60.0
                        meta_path = chosen["path"] / "metadata.json"
                        if meta_path.exists():
                            try:
                                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                                pre_seconds = float(meta.get("pre_seconds", pre_seconds))
                            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                                pass
                        seek_replay_to_timestamp(replay_path, chosen["timestamp"].to_pydatetime() - pd.Timedelta(seconds=pre_seconds))
                        st.session_state.replay_focus_incident = chosen["id"]
                        st.session_state.replay_playing = True
                        st.session_state.replay_history = History(max_rows=max(1200, SETTINGS.history_seconds * 4))
                        st.session_state.event_engine = EventEngine(SETTINGS.process_watchdog_seconds, SETTINGS.opcua_sync_warning_ms)
                        st.session_state.events = []
                        st.rerun()

            start_dt, end_dt = replay.start_timestamp, replay.end_timestamp
            if start_dt and end_dt:
                target_date = st.date_input("Jump date (UTC)", value=start_dt.date(), key="replay_date")
                target_time = st.time_input("Jump time (UTC)", value=start_dt.time().replace(microsecond=0), key="replay_time")
                if st.button("Jump to timestamp", use_container_width=True):
                    seek_replay_to_timestamp(replay_path, datetime.combine(target_date, target_time, tzinfo=timezone.utc))
                    st.rerun()
                st.caption(f"Available: {start_dt:%Y-%m-%d %H:%M:%S} → {end_dt:%Y-%m-%d %H:%M:%S} UTC")
            c1, c2 = st.columns(2)
            if c1.button("⏮ Start", use_container_width=True):
                replay.reset(); st.session_state.replay_playing = False; st.rerun()
            if c2.button("↺ Restart", use_container_width=True):
                replay.reset(); st.session_state.replay_playing = False; st.rerun()
            st.select_slider("Replay speed", options=[1, 2, 5, 10, 25], value=st.session_state.get("replay_speed", 1), format_func=lambda x: f"{x}×", key="replay_speed")
            if st.button("⏯ Play / Pause", use_container_width=True):
                st.session_state.replay_playing = not st.session_state.get("replay_playing", False)
                st.rerun()
            if replay.current_timestamp:
                st.caption(f"Position: {replay.current_timestamp:%Y-%m-%d %H:%M:%S.%f}"[:-3] + " UTC")

st_autorefresh(interval=refresh, key="dashboard_refresh")

# Background live acquisition/recording is deliberately independent of the selected view/source.
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

# Display pipeline. Replay/live/simulation can be changed without touching the recorder.
if mode == SourceMode.REPLAY.value:
    replay = replay_source()
    if replay is None:
        snap = get_sim().snapshot()
    else:
        batch = replay.snapshots_batch(st.session_state.get("replay_speed", 1)) if st.session_state.get("replay_playing") else [replay.snapshot()]
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
    snap = reader.snapshot()
    st.session_state.history = get_live_history()
    st.session_state.event_engine = live_engine
    st.session_state.events = st.session_state.get("live_events", [])

event_engine = get_event_engine()
process_state = event_engine.process_state

if mode == SourceMode.REPLAY.value and st.session_state.get("replay_focus_incident"):
    selected = st.session_state.get("replay_selected_path")
    if selected:
        focus_rows = [r for r in incident_records(Path(selected).parent) if r["id"] == st.session_state["replay_focus_incident"]]
        if focus_rows and not pd.isna(focus_rows[0]["timestamp"]):
            event_ts = focus_rows[0]["timestamp"]
            current_ts = pd.Timestamp(snap.source_timestamp_reference or snap.timestamp)
            st.info(f"🎬 Replay incident **{focus_rows[0]['code']}** · {focus_rows[0]['severity']} · {focus_rows[0]['message']} · event {event_ts.strftime('%H:%M:%S UTC')} · position {(current_ts-event_ts).total_seconds():+.1f}s")

if mode == SourceMode.REPLAY.value:
    history_df = get_replay_history().dataframe()
    analysis_session = None
elif mode == SourceMode.SIMULATION.value:
    history_df = get_history().dataframe()
    analysis_session = selected_session
else:
    history_df = get_live_history().dataframe()
    analysis_session = selected_session

clean, cycles, stages, events_df = analysis_data(snap, history_df, analysis_session)

if mode == SourceMode.REPLAY.value and st.session_state.get("replay_selected_path"):
    recorded_events = load_events(Path(st.session_state["replay_selected_path"]).parent)
    current_ts = pd.Timestamp(snap.source_timestamp_reference or snap.timestamp)
    if not recorded_events.empty and "timestamp" in recorded_events.columns:
        recorded_events = recorded_events[recorded_events["timestamp"] <= current_ts].copy()
        events_df = recorded_events

render_role_visual(view, snap, process_state, clean, cycles, stages, events_df)

if recorder.active:
    st.caption(f"Recording live session: `{recorder.session_dir.name}` · raw telemetry + cleaned analytics + events")
