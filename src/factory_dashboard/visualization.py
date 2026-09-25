from __future__ import annotations

from pathlib import Path
from typing import Iterable
import base64

import plotly.graph_objects as go

from .logic import RegionStatus


REGIONS = {
    "hbw": [(0.57, 0.06), (0.96, 0.04), (0.96, 0.32), (0.55, 0.33)],
    "ms": [(0.34, 0.28), (0.83, 0.26), (0.82, 0.61), (0.33, 0.60)],
    "crane": [(0.12, 0.35), (0.40, 0.34), (0.40, 0.61), (0.12, 0.61)],
    "pm": [(0.06, 0.56), (0.39, 0.57), (0.42, 0.80), (0.05, 0.80)],
    "sl": [(0.13, 0.76), (0.72, 0.75), (0.72, 0.99), (0.10, 0.99)],
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
            fill, line = "rgba(239,68,68,0.48)", "rgba(239,68,68,1)"
        elif status.active:
            fill, line = "rgba(34,197,94,0.30)", "rgba(34,197,94,0.95)"
        else:
            fill, line = "rgba(148,163,184,0.10)", "rgba(148,163,184,0.65)"
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", fill="toself",
            fillcolor=fill, line=dict(color=line, width=3),
            hovertemplate=(f"<b>{status.label}</b><br>{status.detail}"
                           f"<br>Active: {status.active}<br>Fault: {status.fault}<extra></extra>"),
        ))
    return fig


def trend_figure(df, columns: list[str], title: str, height: int = 280) -> go.Figure:
    fig = go.Figure()
    for col in columns:
        if col in df.columns:
            values = df[col]
            fig.add_trace(go.Scatter(x=df["timestamp"], y=values, mode="lines", name=col))
    fig.update_layout(
        title=title, margin=dict(l=20, r=20, t=50, b=20), height=height,
        legend=dict(orientation="h"), hovermode="x unified",
    )
    return fig
