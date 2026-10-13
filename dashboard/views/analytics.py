import plotly.express as px
import streamlit as st

import data
import ui

ui.header("Analytics", "Explore each dataset through the aggregates its schema defines. Public "
          "datasets are snapshots, so you see the latest version; incremental datasets (sales) "
          "are combined across every processed file.")
jobs = ui.load_jobs_or_stop()
done = jobs[(jobs.status == "SUCCEEDED") & jobs.report_key.notna()]
if done.empty:
    ui.empty_state("No successful jobs yet.")
    st.stop()

datasets = sorted(done.dataset.dropna().unique())
dataset = st.segmented_control("Dataset", datasets, default=ui.busiest(done))
if dataset is None:
    st.info("Pick a dataset.")
    st.stop()

selected = done[done.dataset == dataset]
try:
    latest = data.load_report(selected.iloc[0].report_key)
except Exception:  # noqa: BLE001
    ui.empty_state("The latest report for this dataset has expired (30-day lifecycle rule).")
    st.stop()

specs = latest.get("aggregate_specs", {})
snapshot = latest.get("load_type") == "snapshot"
description = data.schema_description(dataset)
if description:
    st.caption(description)

if snapshot:
    frames = data.aggregate_frames(latest.get("aggregates", {}), specs)
    st.info(f"Snapshot dataset: showing the version processed at "
            f"{selected.iloc[0].started_at:%d %b %Y, %H:%M} (job `{latest['job_id']}`, "
            f"{latest['counts']['accepted']:,} clean rows). Older versions stay in S3 "
            "(versioned raw/) and in the Jobs page.", icon="🗂️")
    metrics = latest.get("metrics", {})
    columns = st.columns(min(4, max(1, len(metrics))))
    for i, (name, value) in enumerate(metrics.items()):
        func = latest.get("metric_specs", {}).get(name, "")
        columns[i % len(columns)].metric(ui.title(name), ui.fmt_number(value),
                                         help=f"{func} of {latest.get('measure')}")
else:
    reports = data.load_reports(tuple(selected.head(200).report_key))
    frames = data.combined_frames(reports, specs)
    rows = sum(r["counts"]["accepted"] for r in reports)
    daily = [df for kind, df in frames.values() if kind == "daily"]
    total = daily[0].total.sum() if daily else None
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Files", ui.fmt_int(len(reports)))
    c2.metric("Clean rows", ui.fmt_int(rows))
    if total is not None:
        c3.metric("Total value", f"{total:,.2f}")
        c4.metric("Average per row", f"{total / rows:,.2f}" if rows else "—")

if not frames:
    ui.empty_state("This dataset's schema defines no aggregates.")
    st.stop()


def chart_title(name: str) -> str:
    spec = specs.get(name, {})
    func = spec.get("func", "sum")
    where = ", ".join(f"{k} = {v}" for k, v in (spec.get("where") or {}).items())
    excluded = ", ".join(f"{n} {k} values excluded" for k, n in
                         (spec.get("exclude") or {}).items())
    return (ui.title(name) + f"  ·  {func}" + (f"  ·  {where}" if where else "")
            + (f"  ·  {excluded}" if excluded else ""))


line_charts = [(n, df) for n, (kind, df) in frames.items() if kind in ("daily", "yearly")]
group_charts = [(n, df) for n, (kind, df) in frames.items() if kind == "group"]

for name, df in line_charts:
    if "date" in df:
        fig = px.area(df, x="date", y="total", title=chart_title(name), hover_data=["count"])
        st.plotly_chart(ui.style(fig, 360, dates=True), width="stretch")
    else:
        fig = px.line(df, x="year", y="value", title=chart_title(name), markers=len(df) < 40)
        fig.update_traces(line_color=ui.ACCENT)
        st.plotly_chart(ui.style(fig, 360), width="stretch")

columns = st.columns(2)
for i, (name, df) in enumerate(group_charts):
    top = df.head(15)
    additive = specs.get(name, {}).get("func", "sum") in ("sum", "count")
    if additive and len(df) <= 6 and (top.value >= 0).all():  # parts of a whole only
        fig = px.pie(top, names="key", values="value", title=chart_title(name), hole=0.45)
    else:
        fig = px.bar(top, x="value", y="key", orientation="h", title=chart_title(name)
                     + (f"  ·  top 15 of {len(df)}" if len(df) > 15 else ""))
        fig.update_yaxes(autorange="reversed")
    columns[i % 2].plotly_chart(ui.style(fig, 420 if len(top) > 8 else 340), width="stretch")

with st.expander("Source jobs"):
    st.dataframe(selected[["started_at", "file", "rows_accepted", "acceptance_rate", "job_id"]],
                 hide_index=True, width="stretch")
