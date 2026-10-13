"""Streamlit entry point:  streamlit run dashboard/app.py

Runs locally as the read-only etl-dashboard IAM identity (AWS_PROFILE in dashboard/.env).
"""

import data
import streamlit as st

st.set_page_config(page_title="ETL Pipeline", page_icon="🧪", layout="wide")

pages = {
    "Pipeline": [
        st.Page("views/overview.py", title="Overview", icon="📊", default=True),
        st.Page("views/jobs.py", title="Jobs", icon="🗂️"),
        st.Page("views/data_quality.py", title="Data quality", icon="🧹"),
        st.Page("views/analytics.py", title="Analytics", icon="📈"),
        st.Page("views/operations.py", title="Operations", icon="🛠️"),
    ],
    "V2": [st.Page("views/athena.py", title="Athena SQL", icon="🔎")],
}
navigation = st.navigation(pages)

with st.sidebar:
    try:
        cfg = data.config()
        st.caption(f"**Bucket** `{cfg.bucket}`  \n**Table** `{cfg.table}`  \n"
                   f"**Region** `{cfg.region}`  \n**Profile** `{cfg.profile or 'default'}`")
    except data.ConfigError:
        st.caption("Not configured - see the setup message.")
    if st.button("↻ Refresh data", width="stretch"):
        data.refresh()
        st.rerun()
    st.caption("Data is cached for 30 s.")

navigation.run()
