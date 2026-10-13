import plotly.express as px
import streamlit as st
import ui

ui.header("Overview", "Every CSV dropped into raw/ becomes one job: queued in SQS, processed by "
          "Lambda, tracked in DynamoDB.")
jobs = ui.load_jobs_or_stop()
if jobs.empty:
    ui.empty_state("No jobs yet. Upload a CSV from the Operations page or run "
                   "`python scripts/upload.py sample_data/dirty_small.csv`.")
    st.stop()

done = jobs[jobs.status == "SUCCEEDED"]
failed = jobs[jobs.status == "FAILED"]
rows_total, rows_accepted = done.rows_total.sum(), done.rows_accepted.sum()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Files processed", ui.fmt_int(len(done)))
c2.metric("Failed jobs", ui.fmt_int(len(failed)),
          delta=f"{len(failed)} need attention" if len(failed) else None,
          delta_color="inverse")
c3.metric("Rows processed", ui.fmt_int(rows_total))
c4.metric("Acceptance rate", f"{100 * rows_accepted / rows_total:.2f}%" if rows_total else "—")
c5.metric("Avg job duration", ui.fmt_ms(done.duration_ms.mean()),
          help="p95: " + ui.fmt_ms(done.duration_ms.quantile(0.95)) if len(done) else None)

left, right = st.columns(2)
per_day = (jobs.dropna(subset=["started_at"])
           .assign(day=lambda d: d.started_at.dt.floor("D"))
           .groupby(["day", "status"]).size().rename("jobs").reset_index())
fig = px.bar(per_day, x="day", y="jobs", color="status", title="Jobs per day",
             color_discrete_map=ui.STATUS_COLORS)
left.plotly_chart(ui.style(fig, dates=True), width="stretch")

rows_per_day = (done.dropna(subset=["started_at"])
                .assign(day=lambda d: d.started_at.dt.floor("D"))
                .groupby("day")[["rows_accepted", "rows_rejected"]].sum().reset_index()
                .melt(id_vars="day", var_name="rows", value_name="count"))
rows_per_day["rows"] = rows_per_day["rows"].str.replace("rows_", "")
fig = px.bar(rows_per_day, x="day", y="count", color="rows", title="Rows per day",
             color_discrete_map={"accepted": ui.STATUS_COLORS["SUCCEEDED"],
                                 "rejected": ui.STATUS_COLORS["FAILED"]})
right.plotly_chart(ui.style(fig, dates=True), width="stretch")

st.subheader("Latest jobs")
latest = ui.with_badges(jobs.head(10))[
    ["started_at", "status", "dataset", "file", "rows_total", "acceptance_rate", "duration_ms",
     "attempts", "job_id"]]
st.dataframe(latest, hide_index=True, width="stretch", column_config={
    "started_at": st.column_config.DatetimeColumn("Started", format="DD MMM, HH:mm:ss"),
    "rows_total": st.column_config.NumberColumn("Rows"),
    "acceptance_rate": st.column_config.ProgressColumn("Accepted %", min_value=0,
                                                       max_value=100, format="%.1f%%"),
    "duration_ms": st.column_config.NumberColumn("Duration (ms)"),
})
