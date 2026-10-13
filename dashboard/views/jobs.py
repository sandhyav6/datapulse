import json

import pandas as pd
import plotly.express as px
import streamlit as st

import data
import ui

ui.header("Jobs", "Job history from DynamoDB (status-index). Select a row to see its report.")
jobs = ui.load_jobs_or_stop()
if jobs.empty:
    ui.empty_state("No jobs yet.")
    st.stop()

f1, f2, f3 = st.columns([2, 2, 3])
statuses = f1.multiselect("Status", data.STATUSES, default=list(data.STATUSES))
datasets = sorted(jobs.dataset.dropna().unique())
chosen = f2.multiselect("Dataset", datasets, default=datasets)
search = f3.text_input("Search file or job id", placeholder="e.g. dirty_small")

view = jobs[jobs.status.isin(statuses) & jobs.dataset.isin(chosen)]
if search:
    needle = search.lower()
    view = view[view.file.str.lower().str.contains(needle, regex=False)
                | view.job_id.str.contains(needle, regex=False)]
view = view.reset_index(drop=True)

table = ui.with_badges(view)[
    ["status", "started_at", "dataset", "file", "rows_total", "rows_accepted", "rows_rejected",
     "acceptance_rate", "duration_ms", "attempts", "error_type", "job_id"]]
event = st.dataframe(
    table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
    column_config={
        "started_at": st.column_config.DatetimeColumn("Started", format="DD MMM YYYY, HH:mm:ss"),
        "rows_total": st.column_config.NumberColumn("Rows"),
        "rows_accepted": st.column_config.NumberColumn("Accepted"),
        "rows_rejected": st.column_config.NumberColumn("Rejected"),
        "acceptance_rate": st.column_config.ProgressColumn("Accepted %", min_value=0,
                                                           max_value=100, format="%.1f%%"),
        "duration_ms": st.column_config.NumberColumn("Duration (ms)"),
        "error_type": "Error",
    })
st.caption(f"{len(view)} of {len(jobs)} jobs. Click a column header to sort.")

selected = event.selection.rows if event and event.selection else []
if not selected:
    st.info("Select a job above to see its details.", icon="👆")
    st.stop()

job = view.iloc[selected[0]]
cfg = data.config()
st.divider()
st.subheader(f"{ui.badge(job.status)}  ·  {job.file}")
st.caption(f"job_id `{job.job_id}` · source `s3://{cfg.bucket}/{job.source_key}`")

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Rows", ui.fmt_int(job.rows_total))
m2.metric("Accepted", ui.fmt_int(job.rows_accepted))
m3.metric("Rejected", ui.fmt_int(job.rows_rejected))
m4.metric("Duration", ui.fmt_ms(job.duration_ms))
m5.metric("Attempts", ui.fmt_int(job.attempts),
          help="Deliveries that claimed this job. >1 means it was retried or redriven.")

if job.status == "FAILED" or isinstance(job.error_message, str):
    st.error(f"**{job.error_type}**: {job.error_message}")

links = []
if cfg.log_group:
    links.append(f"[CloudWatch logs for this job]"
                 f"({data.logs_console_url(cfg.region, cfg.log_group, job.job_id)})")
for name, uri in (job.outputs or {}).items():
    links.append(f"**{name}** `{uri}` ([console]({data.s3_console_url(cfg.region, uri)}))")
if links:
    st.markdown("  \n".join(links))

left, right = st.columns(2)
reasons = pd.DataFrame(list((job.rejection_reasons or {}).items()), columns=["reason", "rows"])
if not reasons.empty:
    fig = px.bar(reasons.sort_values("rows"), x="rows", y="reason", orientation="h",
                 title="Rejection reasons", color_discrete_sequence=[ui.STATUS_COLORS["FAILED"]])
    left.plotly_chart(ui.style(fig, 300), width="stretch")
if job.metrics:
    right.markdown("**Metrics**")
    right.json(job.metrics)

tab_report, tab_rejected = st.tabs(["Full report", "Rejected rows (sample)"])
with tab_report:
    if isinstance(job.report_key, str):
        try:
            st.json(data.load_report(job.report_key), expanded=1)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Report unavailable ({type(exc).__name__}); reports expire after 30 "
                       "days.")
    else:
        st.caption("Failed jobs have no report.")
with tab_rejected:
    uri = (job.outputs or {}).get("rejected")
    if uri and job.rows_rejected:
        try:
            st.dataframe(data.load_csv_sample(uri), hide_index=True, width="stretch")
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Rejected file unavailable ({type(exc).__name__}).")
    else:
        st.caption("No rejected rows.")

with st.expander("Raw DynamoDB item"):
    st.code(json.dumps(job.drop(["file"]).to_dict(), indent=2, default=str), language="json")
