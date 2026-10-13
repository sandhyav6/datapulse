import pandas as pd
import streamlit as st

import data
import ui

ui.header("Sources", "Public CSV datasets the pipeline ingests automatically. A daily schedule "
          "(EventBridge Scheduler, 06:30 IST) runs the dataset-ingester, which downloads each "
          "source and writes it to raw/<dataset>/ only if its content changed.")
jobs = ui.load_jobs_or_stop()
cfg = data.config()
sources = data.load_sources()
latest = data.latest_jobs(jobs).set_index("dataset") if len(jobs) else pd.DataFrame()

for source in sources:
    dataset = source["dataset"]
    job = latest.loc[dataset] if dataset in latest.index else None
    with st.container(border=True):
        left, right = st.columns([3, 2])
        left.subheader(ui.title(dataset))
        description = data.schema_description(dataset)
        if description:
            left.caption(description)
        left.markdown(f"**Publisher:** {source.get('publisher', '—')}  \n"
                      f"**License:** {source.get('license', '—')}  \n"
                      f"**Source:** [{source['url']}]({source['url']})  \n"
                      f"**Lands in:** `raw/{dataset}/{dataset}.csv`")
        if job is None:
            right.info("Not ingested yet.")
        else:
            right.metric("Latest job", ui.badge(job.status))
            r1, r2 = right.columns(2)
            r1.metric("Rows", ui.fmt_int(job.rows_total))
            r2.metric("Accepted", "—" if pd.isna(job.acceptance_rate)
                      else f"{job.acceptance_rate:.2f}%")
            right.caption(f"Processed {job.started_at:%d %b %Y, %H:%M} · job `{job.job_id}`")

st.subheader("Fetch now")
st.caption("Runs the ingester immediately instead of waiting for the schedule. Unchanged "
           "sources are skipped by their sha256, so this is always safe to press.")
if not cfg.ingester_function:
    st.info("Set INGESTER_FUNCTION in dashboard/.env (run scripts/make_dashboard_env.py) to "
            "enable this button.")
else:
    choice = st.multiselect("Datasets", [s["dataset"] for s in sources],
                            default=[s["dataset"] for s in sources])
    if st.button("Fetch selected datasets", type="primary", disabled=not choice):
        try:
            status = data.trigger_ingest(data.client("lambda"), cfg.ingester_function, choice)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not start the ingester: {type(exc).__name__}: {exc}")
        else:
            st.success(f"Ingester started (HTTP {status}). Changed datasets appear as new jobs "
                       "within a minute; press Refresh data.")
