from __future__ import annotations

from pathlib import Path
from typing import Iterable
import base64

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .logic import RegionStatus

TUM_BLUE = "#0065BD"
LIGHT_BLUE = "#98C6EA"
ORANGE = "#F28C28"
ORANGE_DARK = "#B34700"
GREEN = "#2E8B57"
GREY = "#B0B0B5"
INK = "#1D1D1F"
MUTED = "#595960"
GRID = "#E5E5EA"


def _base_fig(height: int = 300) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=12, b=10),
        font=dict(family="Arial, sans-serif", color=INK),
        showlegend=False,
        hoverlabel=dict(font_family="Arial, sans-serif"),
    )
    return fig


def _clean_axes(fig: go.Figure, *, x_title: str | None = None, y_title: str | None = None, y_range=None) -> None:
    fig.update_xaxes(
        title=x_title,
        showgrid=False,
        showline=False,
        zeroline=False,
        ticks="",
        tickfont=dict(size=12, color=MUTED),
        title_font=dict(size=13, color=MUTED),
    )
    fig.update_yaxes(
        title=y_title,
        showgrid=True,
        gridcolor=GRID,
        gridwidth=1,
        showline=False,
        zeroline=False,
        ticks="",
        tickfont=dict(size=12, color=MUTED),
        title_font=dict(size=13, color=MUTED),
        range=y_range,
    )


# Normalised coordinates for the supplied factory overview image. The regions
# intentionally represent the physical process areas, not individual sensors.
REGIONS = {
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
    fig = _base_fig(610)
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    suffix = image_path.suffix.lower().lstrip(".") or "jpeg"
    source = f"data:image/{suffix};base64,{encoded}"
    fig.add_layout_image(
        dict(source=source, xref="paper", yref="paper", x=0, y=1, sizex=1, sizey=1,
             sizing="stretch", layer="below")
    )
    fig.update_xaxes(range=[0, 1], visible=False, fixedrange=True)
    fig.update_yaxes(range=[1, 0], visible=False, fixedrange=True)
    for status in statuses:
        pts = REGIONS.get(status.key, [])
        if not pts:
            continue
        xs = [p[0] for p in pts] + [pts[0][0]]
        ys = [p[1] for p in pts] + [pts[0][1]]
        if status.fault:
            fill, line = "rgba(242,140,40,0.50)", ORANGE_DARK
            state = "ATTENTION"
        elif status.active:
            fill, line = "rgba(0,101,189,0.30)", TUM_BLUE
            state = "ACTIVE"
        else:
            fill, line = "rgba(148,163,184,0.10)", "rgba(148,163,184,0.65)"
            state = "IDLE"
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", fill="toself", fillcolor=fill,
            line=dict(color=line, width=3),
            hovertemplate=f"<b>{status.label}</b><br>{status.detail}<br>Status: {state}<extra></extra>",
            showlegend=False,
        ))
        label_x, label_y = REGION_LABEL_POSITIONS[status.key]
        fig.add_annotation(
            x=label_x, y=label_y, xref="paper", yref="paper", text=f"<b>{status.label}</b><br>{state}",
            showarrow=False, font=dict(size=11, color=INK), align="center",
            bgcolor="rgba(255,255,255,0.90)", bordercolor="rgba(229,229,234,0.95)", borderpad=4,
        )
    return fig


def cycle_time_figure(cycles: pd.DataFrame, target_s: float = 55.0, height: int = 320) -> go.Figure:
    """Cake material-flow cycle bars: HBW pickup -> sorting-line entry.

    Pickup cadence is shown when available because it is the useful production
    pacing measure. The actual cycle duration remains a separate physical KPI.
    """
    fig = _base_fig(height)
    if cycles is None or cycles.empty:
        fig.add_annotation(text="No completed cake cycles in this window", x=.5, y=.5, showarrow=False,
                           font=dict(size=16, color=MUTED))
        return fig
    data = cycles.copy()
    value_col = "pickup_interval_s" if "pickup_interval_s" in data.columns and data["pickup_interval_s"].notna().any() else "cycle_time_s"
    data = data.dropna(subset=[value_col]).copy()
    if data.empty:
        return fig
    data["cycle"] = pd.to_numeric(data["cycle"], errors="coerce")
    vals = pd.to_numeric(data[value_col], errors="coerce")
    late = vals > target_s * 1.5
    fig.add_trace(go.Bar(
        x=data.loc[~late, "cycle"], y=vals.loc[~late],
        marker_color=TUM_BLUE,
        text=[f"{v:.0f} s" for v in vals.loc[~late]], textposition="outside",
        hovertemplate="Cake %{x}<br>%{y:.1f} s<extra></extra>",
        name="On time",
    ))
    if late.any():
        late_vals = vals.loc[late]
        fig.add_trace(go.Bar(
            x=data.loc[late, "cycle"], y=late_vals,
            marker_color=ORANGE,
            marker_pattern_shape="/", marker_pattern_fgcolor="#FFFFFF", marker_pattern_size=7,
            text=[f"{v:.0f} s / +{max(v-target_s,0):.0f} late" for v in late_vals], textposition="outside",
            hovertemplate="Cake %{x}<br>%{y:.1f} s<extra></extra>", name="Late",
        ))
    ymax = max(float(vals.max()) * 1.18, target_s * 1.25)
    fig.add_hline(y=target_s, line_color=GREY, line_dash="dash", line_width=2,
                  annotation_text=f"target {target_s:.0f} s", annotation_position="top left",
                  annotation_font_color=MUTED)
    fig.update_layout(bargap=.28, margin=dict(l=25, r=15, t=24, b=40), yaxis_range=[0, ymax])
    _clean_axes(fig, x_title="cake #", y_title=None)
    fig.update_yaxes(showticklabels=False, showgrid=False)
    return fig


def simulated_temperature_figure(df: pd.DataFrame, height: int = 320) -> go.Figure:
    """Show a deterministic dummy oven-temperature signal for the cake line.

    The PLC has no temperature sensor in the supplied data, so this is clearly
    presented as simulated process data rather than measured plant telemetry.
    The target range is intentionally realistic for cake baking: 175–185 °C.
    """
    fig = _base_fig(height)
    if df is None or df.empty:
        return fig
    time_col = "analysis_timestamp" if "analysis_timestamp" in df.columns else "timestamp"
    if time_col not in df.columns:
        return fig
    x = pd.to_datetime(df[time_col], utc=True, errors="coerce")
    valid = x.notna()
    x = x.loc[valid]
    if x.empty:
        return fig
    data = df.loc[valid]
    t = (x - x.iloc[0]).dt.total_seconds().fillna(0.0).to_numpy()
    burn = data.get("ms.process.burn", pd.Series(False, index=data.index)).astype(bool).to_numpy()
    door = data.get("ms.valve.oven_door", pd.Series(False, index=data.index)).astype(bool).to_numpy()
    # Deterministic, smooth dummy signal around 180 °C. No random data is used.
    temp = 180 + 2.2*np.sin(t/21.0) + 0.9*np.sin(t/6.5) - 3.0*door + 0.8*burn
    fig.add_hrect(y0=175, y1=185, fillcolor="rgba(176,176,181,0.20)", line_width=0)
    fig.add_trace(go.Scatter(x=x, y=temp, mode="lines", line=dict(color=TUM_BLUE, width=2.5),
                             hovertemplate="%{x|%H:%M:%S}<br>%{y:.1f} °C<extra></extra>"))
    if len(temp):
        fig.add_annotation(x=x.iloc[-1], y=float(temp[-1]), text=f"<b>{temp[-1]:.1f} °C</b>",
                           showarrow=False, xanchor="left", xshift=8, font=dict(size=16, color=TUM_BLUE))
    fig.add_annotation(xref="paper", yref="y", x=0.01, y=175.4, text="target 175–185 °C",
                       showarrow=False, font=dict(size=12, color=MUTED), xanchor="left")
    fig.update_layout(margin=dict(l=35, r=65, t=10, b=35))
    _clean_axes(fig, y_range=[170, 190])
    return fig

def qa_burn_figure(stages: pd.DataFrame, height: int = 330, burn_lo: float = 3.0, burn_hi: float = 7.0) -> go.Figure:
    fig = _base_fig(height)
    if stages is None or stages.empty:
        return fig
    data = stages[(stages.get("measurement") == "stage_elapsed") & (stages.get("stage") == "Baking")].copy()
    data = data.dropna(subset=["elapsed_s"])
    if data.empty:
        return fig
    data = data.reset_index(drop=True)
    data["cake"] = np.arange(1, len(data) + 1)
    ok = data["elapsed_s"].between(burn_lo, burn_hi, inclusive="both")
    fig.add_hrect(y0=burn_lo, y1=burn_hi, fillcolor="rgba(176,176,181,0.20)", line_width=0)
    fig.add_trace(go.Scatter(x=data.loc[ok, "cake"], y=data.loc[ok, "elapsed_s"], mode="lines+markers",
                             line=dict(color=TUM_BLUE, width=2), marker=dict(size=8, color=TUM_BLUE, symbol="circle"),
                             hovertemplate="Cake %{x}<br>%{y:.1f} s<extra></extra>"))
    nok = data.loc[~ok]
    if not nok.empty:
        fig.add_trace(go.Scatter(x=nok["cake"], y=nok["elapsed_s"], mode="markers+text",
                                 text=[f"NOK {v:.0f} s" for v in nok["elapsed_s"]], textposition="top center",
                                 marker=dict(size=11, color=ORANGE, symbol="diamond", line=dict(color=ORANGE_DARK, width=1)),
                                 textfont=dict(color=ORANGE_DARK, size=12),
                                 hovertemplate="Cake %{x}<br>NOK · %{y:.1f} s<extra></extra>"))
    fig.update_layout(margin=dict(l=35, r=15, t=10, b=40))
    _clean_axes(fig, x_title="cake #", y_title="burn time (s)", y_range=[0, 8])
    return fig


def case_study_complaints() -> dict[str, int]:
    """Manual case-study inputs used only for the management story."""
    return {"June": 1, "July": 2, "August": 2, "September": 0}


def complaints_figure(height: int = 300) -> go.Figure:
    data = case_study_complaints()
    months = list(data)
    values = list(data.values())
    fig = _base_fig(height)
    fig.add_trace(go.Bar(
        x=months, y=values, marker_color=LIGHT_BLUE,
        text=[str(v) for v in values], textposition="outside",
        hovertemplate="%{x}: %{y} complaints<extra></extra>",
    ))
    fig.update_layout(margin=dict(l=15, r=15, t=15, b=45), bargap=.32)
    ymax = max(3, max(values, default=0) + 1)
    _clean_axes(fig, x_title=None, y_title=None, y_range=[0, ymax])
    fig.update_yaxes(showticklabels=False, showgrid=False)
    return fig


def cake_flavour_figure(counts: dict[str, int], height: int = 300) -> go.Figure:
    """Finished cake count by flavour, derived from sorting outputs."""
    labels = ["Vanilla", "Strawberry", "Blueberry"]
    values = [int(counts.get(label, 0)) for label in labels]
    flavour_colors = ["#F5F5F2", "#D85B67", "#5B8FD9"]
    fig = _base_fig(height)
    fig.add_trace(go.Bar(
        x=labels, y=values,
        marker_color=flavour_colors,
        marker_line=dict(color=[MUTED, ORANGE_DARK, TUM_BLUE], width=1),
        text=[str(v) for v in values], textposition="outside",
        hovertemplate="%{x}: %{y} cakes<extra></extra>",
    ))
    ymax = max(3, max(values, default=0) + 1)
    fig.update_layout(margin=dict(l=15, r=15, t=15, b=45), bargap=.28)
    _clean_axes(fig, x_title="cake flavour", y_title=None, y_range=[0, ymax])
    fig.update_yaxes(showticklabels=False, showgrid=False)
    return fig


def oee_components_figure(availability: float, performance: float, quality: float, height: int = 300) -> go.Figure:
    labels = ["Availability", "Performance", "Quality"]
    vals = [max(0, min(1, availability))*100, max(0, min(1, performance))*100, max(0, min(1, quality))*100]
    oee = vals[0] * vals[1] * vals[2] / 10000.0
    fig = _base_fig(height)
    colors = [ORANGE if v < 95 else LIGHT_BLUE for v in vals]
    patterns = ["/" if v < 95 else "" for v in vals]
    fig.add_trace(go.Bar(y=labels, x=vals, orientation="h", marker_color=colors,
                         marker_pattern_shape=patterns, text=[f"{v:.0f} %" for v in vals], textposition="outside",
                         hovertemplate="%{y}: %{x:.1f} %<extra></extra>"))
    fig.add_trace(go.Bar(y=["OEE"], x=[oee], orientation="h", marker_color=TUM_BLUE,
                         text=[f"{oee:.0f} %"], textposition="outside", hovertemplate="OEE: %{x:.1f} %<extra></extra>"))
    fig.update_layout(barmode="group", margin=dict(l=100, r=55, t=15, b=25), xaxis_range=[0, 110])
    _clean_axes(fig, x_title=None, y_title=None, y_range=None)
    fig.update_xaxes(showticklabels=False, showgrid=False)
    return fig


def stage_duration_figure(stages: pd.DataFrame, height: int = 360) -> go.Figure:
    fig = _base_fig(height)
    if stages is None or stages.empty:
        fig.add_annotation(text="No observed stage durations", x=.5, y=.5, showarrow=False, font=dict(size=16, color=MUTED))
        return fig
    x = stages[stages.get("measurement") == "stage_elapsed"].dropna(subset=["elapsed_s"]).copy()
    if x.empty:
        return fig
    order = x.groupby("stage")["elapsed_s"].median().sort_values(ascending=True).index.tolist()
    fig.add_trace(go.Box(
        x=x["stage"], y=x["elapsed_s"],
        boxpoints="all", jitter=.25, pointpos=0, marker=dict(color=TUM_BLUE, size=6, opacity=.65),
        line=dict(color=TUM_BLUE), fillcolor="rgba(152,198,234,0.25)",
        hovertemplate="%{x}<br>%{y:.1f} s<extra></extra>",
    ))
    fig.update_layout(margin=dict(l=10, r=10, t=15, b=95), boxmode="group")
    _clean_axes(fig, x_title=None, y_title="seconds")
    fig.update_xaxes(categoryorder="array", categoryarray=order, tickangle=-25)
    return fig


def station_activity_figure(df: pd.DataFrame, height: int = 320) -> go.Figure:
    fig = _base_fig(height)
    if df is None or df.empty:
        return fig
    time_col = "analysis_timestamp" if "analysis_timestamp" in df.columns else "timestamp"
    times = pd.to_datetime(df[time_col], utc=True, errors="coerce")
    dt = times.shift(-1).sub(times).dt.total_seconds().clip(lower=0)
    if len(dt):
        dt.iloc[-1] = 0.0
    total_s = float(dt.sum())
    rows = []
    for region in ("HBW", "Crane", "MS", "PM", "SL"):
        col = f"region.{region}.active"
        if col in df.columns:
            active = df[col].fillna(False).astype(bool)
            share = float(dt.where(active, 0.0).sum() / total_s * 100) if total_s > 0 else float(active.mean()*100)
            rows.append((region, share))
    if rows:
        labels = [r[0] for r in rows]
        vals = [r[1] for r in rows]
        fig.add_trace(go.Bar(x=labels, y=vals, marker_color=TUM_BLUE,
                             text=[f"{v:.0f} %" for v in vals], textposition="outside",
                             hovertemplate="%{x}: %{y:.1f} % active<extra></extra>"))
    fig.update_layout(margin=dict(l=10, r=10, t=15, b=55))
    _clean_axes(fig, x_title=None, y_title="active share (%)", y_range=[0, 100])
    return fig


def event_timeline_figure(events: pd.DataFrame, height: int = 300) -> go.Figure:
    fig = _base_fig(height)
    if events is None or events.empty or "timestamp" not in events.columns:
        return fig
    e = events.copy()
    e["timestamp"] = pd.to_datetime(e["timestamp"], utc=True, errors="coerce")
    e = e.dropna(subset=["timestamp"])
    rank = {"INFO": 1, "WARNING": 2, "CRITICAL": 3}
    e["rank"] = e.get("severity", pd.Series("INFO", index=e.index)).map(rank).fillna(1)
    fig.add_trace(go.Scatter(x=e["timestamp"], y=e["rank"], mode="markers",
                             marker=dict(size=11, color=[ORANGE_DARK if r >= 3 else ORANGE if r == 2 else TUM_BLUE for r in e["rank"]]),
                             text=e.get("message", ""), customdata=e.get("severity", ""),
                             hovertemplate="%{x|%H:%M:%S}<br>%{customdata}<br>%{text}<extra></extra>"))
    fig.update_layout(margin=dict(l=25, r=10, t=10, b=35))
    _clean_axes(fig, y_range=[0.5, 3.5])
    fig.update_yaxes(tickvals=[1,2,3], ticktext=["Info","Warning","Critical"], showgrid=False)
    return fig


def trend_figure(df, columns: list[str], title: str, height: int = 280) -> go.Figure:
    fig = _base_fig(height)
    time_col = "analysis_timestamp" if "analysis_timestamp" in df.columns else "timestamp"
    for col in columns:
        if col in df.columns:
            fig.add_trace(go.Scatter(x=df[time_col], y=df[col], mode="lines", name=col,
                                     line=dict(width=2, color=TUM_BLUE)))
    fig.update_layout(title=title, showlegend=False)
    return fig


# Backward-compatible alias for older integrations.
station_utilization_figure = station_activity_figure
