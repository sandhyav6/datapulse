"""Shared look-and-feel: status badges, colours, chart styling, setup/error screens."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import data

STATUS_COLORS = {"SUCCEEDED": "#2e9e5b", "FAILED": "#d64545", "PROCESSING": "#e0a526"}
STATUS_BADGES = {"SUCCEEDED": "🟢 SUCCEEDED", "FAILED": "🔴 FAILED", "PROCESSING": "🟡 PROCESSING"}
ACCENT = "#3b6fd4"
SEQUENCE = ["#3b6fd4", "#2e9e5b", "#e0a526", "#8e5bd4", "#d64545", "#2aa5b8", "#c26a2b",
            "#6b7a90"]
px.defaults.color_discrete_sequence = SEQUENCE


def style(fig: go.Figure, height: int = 340, dates: bool = False) -> go.Figure:
    fig.update_layout(
        height=height, margin={"l": 10, "r": 10, "t": 70, "b": 10},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "xanchor": "right", "x": 1,
                "title": None},
        colorway=SEQUENCE, hovermode="x unified", template="plotly_white",
        font={"family": "Inter, Segoe UI, sans-serif", "size": 13},
        title={"font": {"size": 15}},
    )
    fig.update_xaxes(showgrid=False, title=None)
    if dates:
        fig.update_xaxes(tickformat="%d %b %Y")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)", title=None)
    return fig


def busiest(jobs: pd.DataFrame) -> str | None:
    """Dataset with the most jobs - the sensible default selection."""
    counts = jobs.dataset.dropna().value_counts()
    return counts.index[0] if len(counts) else None


def header(title: str, caption: str) -> None:
    st.title(title)
    st.caption(caption)


def badge(status: str | None) -> str:
    return STATUS_BADGES.get(status or "", status or "—")


def with_badges(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["status"] = out["status"].map(badge)
    return out


def title(name: str) -> str:
    """'warmest_month_°C' -> 'Warmest month °C' (only the first letter changes case)."""
    text = name.replace("_", " ")
    return text[:1].upper() + text[1:]


def fmt_number(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, int) or float(value).is_integer() and abs(value) >= 1000:
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".") if abs(value) < 1000 else f"{value:,.2f}"


def fmt_int(value) -> str:
    return "—" if value is None or pd.isna(value) else f"{int(value):,}"


def fmt_ms(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value / 1000:.1f} s" if value >= 1000 else f"{int(value)} ms"


def fmt_bytes(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} TB"


def load_jobs_or_stop() -> pd.DataFrame:
    """Load jobs, or show a setup/permissions screen and stop the page."""
    try:
        data.config()
    except data.ConfigError as exc:
        setup_screen(str(exc))
        st.stop()
    try:
        return data.load_jobs()
    except Exception as exc:  # noqa: BLE001 - show any AWS/credential error on screen
        st.error(f"Could not read the jobs table: {type(exc).__name__}: {exc}")
        st.info("Check that AWS_PROFILE points to the etl-dashboard profile, that the stack is "
                "deployed, and that the DashboardReadPolicy is attached to that user.")
        st.stop()


def setup_screen(problem: str) -> None:
    st.warning(f"Dashboard is not configured yet: {problem}")
    st.markdown(
        "Create `dashboard/.env` from the deployed stack, then restart:\n\n"
        "```bash\npython scripts/make_dashboard_env.py\n```\n\n"
        "or copy `dashboard/.env.example` and fill it in by hand.")


def empty_state(message: str) -> None:
    st.info(message, icon="ℹ️")
