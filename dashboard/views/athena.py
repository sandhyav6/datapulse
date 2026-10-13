import data
import plotly.express as px
import streamlit as st
import ui

SAVED_QUERIES = {
    "Monthly revenue": """
SELECT date_format(transaction_date, '%Y-%m') AS month,
       SUM(transaction_amount) AS revenue,
       COUNT(*) AS transactions
FROM sales
GROUP BY 1
ORDER BY 1""",
    "Top 10 categories": """
SELECT product_category,
       SUM(transaction_amount) AS revenue,
       SUM(quantity) AS units
FROM sales
GROUP BY 1
ORDER BY revenue DESC
LIMIT 10""",
    "Region by month": """
SELECT region,
       date_format(transaction_date, '%Y-%m') AS month,
       SUM(transaction_amount) AS revenue
FROM sales
GROUP BY 1, 2
ORDER BY 2, 3 DESC""",
}

ui.header("Athena SQL", "Serverless SQL over the curated Parquet data lake (Glue table `sales`, "
          "partition projection on ingest_date). 100 MB scan cap per query.")
try:
    cfg = data.config()
except data.ConfigError as exc:
    ui.setup_screen(str(exc))
    st.stop()
if not (cfg.athena_workgroup and cfg.glue_database):
    st.info("Athena is part of V2. Deploy with `EnableAnalytics=true`, then re-run "
            "`python scripts/make_dashboard_env.py` to add ATHENA_WORKGROUP and GLUE_DATABASE.",
            icon="🧩")
    st.stop()


def show(sql: str) -> None:
    try:
        df, stats = data.load_athena(sql)
    except Exception as exc:  # noqa: BLE001
        st.error(f"{type(exc).__name__}: {exc}")
        return
    s1, s2, s3 = st.columns(3)
    s1.metric("Rows", ui.fmt_int(len(df)))
    s2.metric("Data scanned", ui.fmt_bytes(stats["bytes_scanned"]))
    s3.metric("Run time", ui.fmt_ms(stats["runtime_ms"]))
    numeric = [c for c in df.columns if df[c].dtype.kind in "if"]
    labels = [c for c in df.columns if c not in numeric]
    if labels and numeric and len(df) > 1:
        color = labels[0] if len(labels) > 1 else None
        x = labels[1] if len(labels) > 1 else labels[0]
        fig = px.bar(df, x=x, y=numeric[0], color=color, barmode="group")
        st.plotly_chart(ui.style(fig), width="stretch")
    st.dataframe(df, hide_index=True, width="stretch")
    st.caption(f"Query id `{stats['query_id']}` · workgroup `{cfg.athena_workgroup}`")


tab_saved, tab_free = st.tabs(["Saved queries", "Write SQL"])
with tab_saved:
    name = st.segmented_control("Query", list(SAVED_QUERIES), default="Monthly revenue")
    if name:
        st.code(SAVED_QUERIES[name].strip(), language="sql")
        if st.button("Run", type="primary", key="run_saved"):
            show(SAVED_QUERIES[name])
with tab_free:
    st.caption("Read-only: a single SELECT (or WITH ... SELECT). Tables: `sales` (Parquet), "
               "`sales_csv` (same rows as CSV - compare the bytes scanned).")
    sql = st.text_area("SQL", "SELECT region, COUNT(*) AS rows, SUM(transaction_amount) AS revenue"
                       "\nFROM sales\nGROUP BY region\nORDER BY revenue DESC", height=160)
    if st.button("Run query", type="primary", key="run_free"):
        try:
            data.validate_select(sql)
        except ValueError as exc:
            st.error(str(exc))
        else:
            show(sql)
