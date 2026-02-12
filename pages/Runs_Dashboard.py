"""
Runs Dashboard - View all test runs and their confusion matrices
"""
import streamlit as st
import pandas as pd
from datetime import datetime
from modules.db import get_runs_collection
from modules.run_names import parse_run_name

st.set_page_config(
    page_title="Runs Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("Runs Dashboard")
st.caption("Track all test runs and compare performance")

# Load all runs from MongoDB
@st.cache_data(ttl=60)
def load_runs():
    """Load all runs from MongoDB."""
    coll = get_runs_collection()
    runs = list(coll.find().sort("timestamp", -1))  # Most recent first
    return runs

runs = load_runs()

if not runs:
    st.info("No test runs found. Run some test cases from the main page to see them here!")
    st.stop()

# ---------------------------------------------------------------------------
# Run Log Table (no scroll — sized to fit)
# ---------------------------------------------------------------------------
st.markdown(f"### Run Log ({len(runs)} runs)")

rows = []
for run in runs:
    run_info = parse_run_name(run.get("run_name", "Unknown"))
    metrics = run.get("metrics", {})

    rows.append({
        "Run Name": run_info["display_name"],
        "Date": run_info["date_str"],
        "Time": run_info["time_str"],
        "Run By": run.get("run_by", ""),
        "Prompt Change": run.get("prompt_label", ""),
        "TCs": run.get("test_cases_run", 0),
        "TP": metrics.get("tp", 0),
        "FP": metrics.get("fp", 0),
        "FN": metrics.get("fn", 0),
        "Precision": f"{metrics.get('precision', 0):.1%}" if metrics.get('precision') else "-",
        "Recall": f"{metrics.get('recall', 0):.1%}" if metrics.get('recall') else "-",
    })

df = pd.DataFrame(rows)
df.index = df.index + 1  # 1-based index

st.table(df)

st.divider()

# ---------------------------------------------------------------------------
# Per-Run Details (tabs)
# ---------------------------------------------------------------------------
st.markdown("### Run Details")

if len(runs) > 0:
    tab_names = [parse_run_name(run.get("run_name", "Unknown"))["display_name"] for run in runs]
    tabs = st.tabs(tab_names[:10])

    for idx, (tab, run) in enumerate(zip(tabs, runs[:10])):
        with tab:
            run_info = parse_run_name(run.get("run_name", "Unknown"))
            metrics = run.get("metrics", {})
            tp = metrics.get("tp", 0)
            fp = metrics.get("fp", 0)
            fn = metrics.get("fn", 0)
            precision = metrics.get("precision", 0)
            recall = metrics.get("recall", 0)
            f1 = metrics.get("f1", 0)

            # --- Colored confusion summary ---
            st.markdown(
                f"""
                <div style="display:flex; gap:12px; margin-bottom:16px;">
                    <div style="background:#e6f4ea; border-left:4px solid #34a853; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">True Positives</div>
                        <div style="font-size:28px; font-weight:700; color:#1e7e34;">{tp}</div>
                    </div>
                    <div style="background:#fce8e6; border-left:4px solid #ea4335; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">False Positives</div>
                        <div style="font-size:28px; font-weight:700; color:#c5221f;">{fp}</div>
                    </div>
                    <div style="background:#fff3e0; border-left:4px solid #f9ab00; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">False Negatives</div>
                        <div style="font-size:28px; font-weight:700; color:#e37400;">{fn}</div>
                    </div>
                    <div style="background:#e8f0fe; border-left:4px solid #4285f4; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">Precision</div>
                        <div style="font-size:28px; font-weight:700; color:#1a73e8;">{precision:.1%}</div>
                    </div>
                    <div style="background:#e8f0fe; border-left:4px solid #4285f4; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">Recall</div>
                        <div style="font-size:28px; font-weight:700; color:#1a73e8;">{recall:.1%}</div>
                    </div>
                    <div style="background:#e8f0fe; border-left:4px solid #4285f4; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">F1 Score</div>
                        <div style="font-size:28px; font-weight:700; color:#1a73e8;">{f1:.1%}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # --- Per-TC breakdown ---
            if run.get("per_tc_metrics"):
                st.markdown("#### Per Test Case")

                tc_rows = []
                for tc_name, tc_metrics in run["per_tc_metrics"].items():
                    tc_rows.append({
                        "Test Case": tc_name,
                        "GT Expected": tc_metrics.get("expected", 0),
                        "API Found": tc_metrics.get("relevant_found", 0),
                        "TP": tc_metrics.get("tp", 0),
                        "FP": tc_metrics.get("fp", 0),
                        "FN": tc_metrics.get("fn", 0),
                    })

                if tc_rows:
                    tc_df = pd.DataFrame(tc_rows)
                    tc_df.index = tc_df.index + 1
                    st.table(tc_df)

# ---------------------------------------------------------------------------
# Run Comparison
# ---------------------------------------------------------------------------
st.divider()
st.markdown("### Run Comparison")

if len(runs) >= 2:
    run_names = [parse_run_name(r.get("run_name", "Unknown"))["display_name"] for r in runs]

    col1, col2 = st.columns(2)
    with col1:
        run1_name = st.selectbox("Select Run 1", run_names, key="run1")
    with col2:
        run2_name = st.selectbox("Select Run 2", run_names[1:] if len(run_names) > 1 else run_names, key="run2")

    run1 = next((r for r in runs if parse_run_name(r.get("run_name", ""))["display_name"] == run1_name), None)
    run2 = next((r for r in runs if parse_run_name(r.get("run_name", ""))["display_name"] == run2_name), None)

    if run1 and run2:
        m1 = run1.get("metrics", {})
        m2 = run2.get("metrics", {})

        col1, col2, col3 = st.columns(3)
        col1.metric("TP", m1.get("tp", 0), delta=m1.get("tp", 0) - m2.get("tp", 0))
        col2.metric("FP", m1.get("fp", 0), delta=m1.get("fp", 0) - m2.get("fp", 0), delta_color="inverse")
        col3.metric("FN", m1.get("fn", 0), delta=m1.get("fn", 0) - m2.get("fn", 0), delta_color="inverse")

        col4, col5, col6 = st.columns(3)

        p1 = m1.get("precision", 0)
        p2 = m2.get("precision", 0)
        col4.metric("Precision", f"{p1:.1%}", delta=f"{(p1-p2):.1%}")

        r1 = m1.get("recall", 0)
        r2 = m2.get("recall", 0)
        col5.metric("Recall", f"{r1:.1%}", delta=f"{(r1-r2):.1%}")

        f1_1 = m1.get("f1", 0)
        f1_2 = m2.get("f1", 0)
        col6.metric("F1 Score", f"{f1_1:.1%}", delta=f"{(f1_1-f1_2):.1%}")
else:
    st.info("Run more test cases to enable comparison!")

# Refresh button
if st.button("Refresh Data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
