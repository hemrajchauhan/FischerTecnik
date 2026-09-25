from __future__ import annotations

from pathlib import Path
from typing import Iterable
import base64

import plotly.graph_objects as go
import pandas as pd

from .logic import RegionStatus


# Normalised image coordinates for the actual stations in factory_overview.jpg.
# The previous PM polygon was on the lower-left area of the photo, although the
# punching machine is physically on the right-hand side of the central cluster.
# These regions intentionally overlap slightly because the physical stations
# are connected by conveyors; the colour is driven by the station's own
# actuator group, not by neighbouring sensors.
REGIONS = {
    # Calibrated against the supplied portrait photo. The polygons cover the
    # mechanical station, not the PLC/electrical cabinet around it.
    "hbw": [(0.50, 0.17), (0.83, 0.17), (0.83, 0.37), (0.50, 0.37)],
    "crane": [(0.03, 0.36), (0.36, 0.36), (0.38, 0.62), (0.03, 0.62)],
    "ms": [(0.28, 0.39), (0.70, 0.39), (0.70, 0.70), (0.28, 0.70)],
    "pm": [(0.72, 0.45), (0.98, 0.45), (0.98, 0.68), (0.72, 0.68)],
    "sl": [(0.08, 0.72), (0.77, 0.72), (0.77, 0.97), (0.08, 0.97)],
}

REGION_LABEL_POSITIONS = {
    "hbw": (0.665, 0.20),
    "crane": (0.20, 0.40),
    "ms": (0.49, 0.42),
    "pm": (0.85, 0.48),
    "sl": (0.42, 0.75),
}


def factory_figure(image_path: Path, statuses: Iterable[RegionStatus]) -> go.Figure:
    fig = go.Figure()
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    suffix = image_path.suffix.lower().lstrip(".") or "jpeg"
    source = f"data:image/{suffix};base64,{encoded}"
    fig.add_layout_image(
        dict(source=source, xref="paper", yref="paper", x=0, y=1, sizex=1, sizey=1,
             sizing="stretch", layer="below")
    )
    fig.update_xaxes(range=[0, 1], visible=False, fixedrange=True)
    fig.update_yaxes(range=[1, 0], visible=False, fixedrange=True)
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0), height=650,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=False,
    )

    for status in statuses:
        pts = REGIONS.get(status.key, [])
        if not pts:
            continue
        xs = [p[0] for p in pts] + [pts[0][0]]
        ys = [p[1] for p in pts] + [pts[0][1]]
        if status.fault:
            fill, line = "rgba(242,140,40,0.46)", "rgba(179,71,0,1)"
        elif status.active:
            fill, line = "rgba(0,101,189,0.28)", "rgba(0,101,189,0.95)"
        else:
            fill, line = "rgba(148,163,184,0.10)", "rgba(148,163,184,0.65)"
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", fill="toself",
            fillcolor=fill, line=dict(color=line, width=3),
            hovertemplate=(f"<b>{status.label}</b><br>{status.detail}"
                           f"<br>Active: {status.active}<br>Fault: {status.fault}<extra></extra>"),
            showlegend=False,
        ))
        label_x, label_y = REGION_LABEL_POSITIONS[status.key]
        fig.add_annotation(
            x=label_x, y=label_y, xref="paper", yref="paper",
            text=f"<b>{status.label}</b><br>{'ACTIVE' if status.active else 'IDLE'}",
            showarrow=False, font=dict(size=11, color="#1D1D1F"), align="center",
            bgcolor="rgba(255,255,255,0.90)", bordercolor="rgba(229,229,234,0.95)", borderpad=3,
        )
    return fig


def trend_figure(df, columns: list[str], title: str, height: int = 280) -> go.Figure:
    fig = go.Figure()
    for col in columns:
        if col in df.columns:
            values = df[col]
            fig.add_trace(go.Scatter(x=df["analysis_timestamp"] if "analysis_timestamp" in df.columns else df["timestamp"], y=values, mode="lines", name=col))
    fig.update_layout(
        title=title, margin=dict(l=20, r=20, t=50, b=20), height=height,
        legend=dict(orientation="h"), hovermode="x unified",
    )
    return fig


def cycle_time_figure(cycles, height: int = 280) -> go.Figure:
    fig = go.Figure()
    if cycles is not None and not cycles.empty:
        fig.add_trace(go.Scatter(
            x=cycles["cycle"], y=cycles["cycle_time_s"], mode="lines+markers",
            name="Cycle time", hovertemplate="Cycle %{x}<br>%{y:.1f}s<extra></extra>"
        ))
        if len(cycles) >= 3:
            rolling = cycles["cycle_time_s"].rolling(3, min_periods=1).median()
            fig.add_trace(go.Scatter(
                x=cycles["cycle"], y=rolling, mode="lines", name="3-cycle median",
                line=dict(dash="dash"), hovertemplate="Median %{y:.1f}s<extra></extra>"
            ))
    fig.update_layout(title="Cycle time", xaxis_title="Cycle", yaxis_title="Seconds", height=height,
                      margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified")
    return fig


def stage_duration_figure(stages, height: int = 330) -> go.Figure:
    fig = go.Figure()
    if stages is None or stages.empty:
        fig.update_layout(title="Observed stage durations", height=height)
        return fig
    x = stages.dropna(subset=["elapsed_s"]).copy()
    value_col = "elapsed_s"
    if not x.empty:
        order = x.groupby("stage")[value_col].median().sort_values().index.tolist()
        for stage in order:
            vals = x.loc[x["stage"] == stage, value_col].dropna()
            fig.add_trace(go.Box(y=vals, name=stage, boxmean=True, boxpoints="outliers"))
    fig.update_layout(title="Observed stage duration distribution", yaxis_title="Seconds",
                      height=height, margin=dict(l=20, r=20, t=50, b=90), showlegend=False)
    return fig


def station_activity_figure(df, height: int = 300) -> go.Figure:
    fig = go.Figure()
    if df is not None and not df.empty:
        rows = []
        time_col = "analysis_timestamp" if "analysis_timestamp" in df.columns else "timestamp"
        times = pd.to_datetime(df[time_col], utc=True, errors="coerce")
        dt = times.shift(-1).sub(times).dt.total_seconds().clip(lower=0)
        if len(dt):
            dt.iloc[-1] = 0.0
        total_s = float(dt.sum())
        for region in ("HBW", "Crane", "MS", "PM", "SL"):
            col = f"region.{region}.active"
            if col in df.columns:
                active = df[col].fillna(False).astype(bool)
                share = float((dt.where(active, 0.0).sum() / total_s) * 100.0) if total_s > 0 else float(active.mean() * 100.0)
                rows.append((region, share))
        if rows:
            fig.add_trace(go.Bar(x=[r[0] for r in rows], y=[r[1] for r in rows],
                                 text=[f"{r[1]:.0f}%" for r in rows], textposition="auto"))
    fig.update_layout(title="Observed station activity", yaxis_title="Active share (%)",
                      yaxis=dict(range=[0, 100]), height=height, margin=dict(l=20, r=20, t=50, b=20))
    return fig


def event_timeline_figure(events, height: int = 300) -> go.Figure:
    fig = go.Figure()
    if events is not None and not events.empty and "timestamp" in events.columns:
        e = events.copy()
        e["timestamp"] = pd.to_datetime(e["timestamp"], utc=True, errors="coerce")
        e = e.dropna(subset=["timestamp"])
        severity_rank = {"INFO": 1, "WARNING": 2, "CRITICAL": 3}
        e["rank"] = e.get("severity", pd.Series("INFO", index=e.index)).map(severity_rank).fillna(1)
        fig.add_trace(go.Scatter(
            x=e["timestamp"], y=e["rank"], mode="markers", text=e.get("message", ""),
            customdata=e.get("severity", ""), hovertemplate="%{x}<br>%{customdata}<br>%{text}<extra></extra>",
            marker=dict(size=10), name="Events"
        ))
    fig.update_layout(title="Events over time", yaxis=dict(tickvals=[1, 2, 3], ticktext=["Info", "Warning", "Critical"]),
                      height=height, margin=dict(l=20, r=20, t=50, b=20))
    return fig


# Backward-compatible alias for older integrations.
station_utilization_figure = station_activity_figure
