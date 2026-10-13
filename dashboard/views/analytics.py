import data
import plotly.express as px
import streamlit as st
import ui

ui.header("Analytics", "Business metrics built from each job's report aggregates - no table "
          "scans, no extra queries. Totals cover every successfully processed file.")
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
selected = done[done.dataset == dataset].head(200)
reports = data.load_reports(tuple(selected.report_key))
if not reports:
    ui.empty_state("Reports for these jobs have expired (30-day lifecycle rule).")
    st.stop()

aggregates = data.combine_aggregates(reports)
daily = {name: df for name, df in aggregates.items() if "date" in df}
groups = {name: df for name, df in aggregates.items() if "key" in df}


def title(name: str) -> str:
    return name.replace("_", " ").capitalize()


rows = sum(r["counts"]["accepted"] for r in reports)
total = next(iter(daily.values())).total.sum() if daily else None
c1, c2, c3, c4 = st.columns(4)
c1.metric("Files", ui.fmt_int(len(reports)))
c2.metric("Clean rows", ui.fmt_int(rows))
if total is not None:
    c3.metric("Total value", f"{total:,.2f}")
    c4.metric("Average per row", f"{total / rows:,.2f}" if rows else "—")

for name, df in daily.items():
    fig = px.area(df, x="date", y="total", title=title(name), hover_data=["count"])
    fig.update_traces(line_color=ui.ACCENT)
    st.plotly_chart(ui.style(fig, 360, dates=True), width="stretch")

columns = st.columns(2)
for i, (name, df) in enumerate(groups.items()):
    if i % 2 == 0:
        fig = px.bar(df, x="total", y="key", orientation="h", title=title(name))
        fig.update_yaxes(autorange="reversed")
    else:
        fig = px.pie(df, names="key", values="total", title=title(name), hole=0.45)
    columns[i % 2].plotly_chart(ui.style(fig), width="stretch")

with st.expander("Source jobs"):
    st.dataframe(selected[["started_at", "file", "rows_accepted", "job_id"]], hide_index=True,
                 width="stretch")
