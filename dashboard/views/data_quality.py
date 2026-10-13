import plotly.express as px
import streamlit as st

import data
import ui

ui.header("Data quality", "Why rows are rejected, per job and over time. Every reason is a "
          "machine-readable code from the dataset schema rules.")
jobs = ui.load_jobs_or_stop()
done = jobs[jobs.status == "SUCCEEDED"]
if done.empty:
    ui.empty_state("No successful jobs yet.")
    st.stop()

datasets = sorted(done.dataset.dropna().unique())
dataset = st.segmented_control("Dataset", datasets, default=ui.busiest(done))
if dataset is None:
    st.info("Pick a dataset.")
    st.stop()
done = done[done.dataset == dataset]
reasons = data.reason_counts(done)

c1, c2, c3, c4 = st.columns(4)
total, accepted = done.rows_total.sum(), done.rows_accepted.sum()
c1.metric("Rows checked", ui.fmt_int(total))
c2.metric("Rows rejected", ui.fmt_int(total - accepted))
c3.metric("Acceptance rate", f"{100 * accepted / total:.2f}%" if total else "—")
c4.metric("Distinct issue types", ui.fmt_int(reasons.reason.nunique()))

left, right = st.columns(2)
if reasons.empty:
    left.success("No rejected rows for this dataset.")
else:
    by_reason = reasons.groupby("reason")["count"].sum().sort_values().reset_index()
    fig = px.bar(by_reason, x="count", y="reason", orientation="h", title="Rejections by reason",
                 color_discrete_sequence=[ui.STATUS_COLORS["FAILED"]])
    left.plotly_chart(ui.style(fig), width="stretch")

trend = done.dropna(subset=["started_at"]).sort_values("started_at")
fig = px.line(trend, x="started_at", y="acceptance_rate", markers=True,
              title="Acceptance rate per job", hover_data=["file", "rows_total"])
fig.update_yaxes(range=[min(80, trend.acceptance_rate.min() - 2) if len(trend) else 0, 100.5],
                 ticksuffix="%")
fig.update_traces(line_color=ui.ACCENT)
fig.update_xaxes(tickformat="%d %b %H:%M")
right.plotly_chart(ui.style(fig), width="stretch")

if not reasons.empty:
    per_job = reasons.assign(label=reasons.file + " · " + reasons.job_id.str[:6])
    fig = px.bar(per_job, x="label", y="count", color="reason", title="Rejection reasons per job")
    st.plotly_chart(ui.style(fig, 380), width="stretch")

    over_time = (reasons.dropna(subset=["started_at"])
                 .assign(day=lambda d: d.started_at.dt.floor("D"))
                 .groupby(["day", "reason"])["count"].sum().reset_index())
    fig = px.area(over_time, x="day", y="count", color="reason", title="Rejections over time")
    st.plotly_chart(ui.style(fig, dates=True), width="stretch")

st.subheader("Inspect rejected rows")
candidates = done[done.rows_rejected > 0]
if candidates.empty:
    st.caption("No job has rejected rows.")
else:
    labels = {f"{r.file} · {r.job_id} ({ui.fmt_int(r.rows_rejected)} rejected)": r
              for r in candidates.itertuples()}
    choice = labels[st.selectbox("Job", list(labels))]
    uri = (choice.outputs or {}).get("rejected")
    if uri:
        try:
            st.dataframe(data.load_csv_sample(uri), hide_index=True, width="stretch")
            st.caption(f"First 50 rows of `{uri}` - original values plus a `reasons` column.")
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Rejected file unavailable ({type(exc).__name__}); it expires after "
                       "30 days.")
