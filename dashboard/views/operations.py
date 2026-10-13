import data
import streamlit as st
import ui

ui.header("Operations", "Queue health, failed jobs and uploads. The dashboard can upload to "
          "raw/ but can never change outputs or job state.")
jobs = ui.load_jobs_or_stop()
cfg = data.config()

st.subheader("Queues")
try:
    depths = data.load_queue_depths()
except Exception as exc:  # noqa: BLE001
    depths = {"waiting": None, "in_flight": None, "dlq": None}
    st.warning(f"Cannot read queue attributes ({type(exc).__name__}). Is QUEUE_URL/DLQ_URL set "
               "and sqs:GetQueueAttributes allowed?")
q1, q2, q3, q4 = st.columns(4)
q1.metric("Waiting in queue", ui.fmt_int(depths["waiting"]))
q2.metric("Being processed", ui.fmt_int(depths["in_flight"]))
q3.metric("Dead-letter queue", ui.fmt_int(depths["dlq"]))
q4.metric("Jobs in PROCESSING", ui.fmt_int((jobs.status == "PROCESSING").sum()) if len(jobs)
          else "0")
if depths["dlq"]:
    st.error(f"{depths['dlq']} message(s) in the DLQ - DlqDepthAlarm is firing. Fix the cause "
             "(schema, code or file), then redrive with `scripts/redrive_dlq.sh`.")
elif depths["dlq"] == 0:
    st.success("DLQ is empty.")

st.subheader("Failed jobs")
failed = jobs[jobs.status == "FAILED"] if len(jobs) else jobs
if failed.empty:
    st.caption("No failed jobs.")
else:
    st.dataframe(
        failed[["finished_at", "dataset", "file", "attempts", "error_type", "error_message",
                "job_id"]],
        hide_index=True, width="stretch", column_config={
            "finished_at": st.column_config.DatetimeColumn("Failed at",
                                                           format="DD MMM, HH:mm:ss"),
            "error_message": st.column_config.TextColumn("Error", width="large"),
        })

retrying = jobs[(jobs.status == "PROCESSING") & jobs.error_type.notna()] if len(jobs) else jobs
if not retrying.empty:
    st.subheader("Retrying")
    st.dataframe(retrying[["started_at", "file", "attempts", "error_type", "error_message"]],
                 hide_index=True, width="stretch")

st.subheader("Upload a CSV")
st.caption("The file goes to `raw/<dataset>/<name>.csv`; S3 notifies SQS and a new job appears "
           "within seconds. Re-uploading identical bytes under the same name is the same job.")
known = sorted(set(jobs.dataset.dropna()) - {"unknown"} | {"sales"}) if len(jobs) else ["sales"]
with st.form("upload", clear_on_submit=True):
    u1, u2 = st.columns([1, 3])
    default = ui.busiest(jobs) if len(jobs) else "sales"
    dataset = u1.selectbox("Dataset", known + ["other..."],
                           index=known.index(default) if default in known else 0)
    other = u1.text_input("Other dataset name", placeholder="returns")
    upload = u2.file_uploader("CSV file (max 5 MB)", type=["csv"])
    submitted = st.form_submit_button("Upload to raw/", type="primary")

if submitted:
    name = other.strip() if dataset == "other..." else dataset
    if upload is None:
        st.warning("Choose a file first.")
    else:
        try:
            result = data.upload_csv(data.client("s3"), cfg.bucket, name, upload.name,
                                     upload.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Upload failed: {type(exc).__name__}: {exc}")
        else:
            data.refresh()
            st.success(f"Uploaded `s3://{cfg.bucket}/{result['key']}` - job "
                       f"`{result['job_id']}`. Press **Refresh data** in a few seconds.")
