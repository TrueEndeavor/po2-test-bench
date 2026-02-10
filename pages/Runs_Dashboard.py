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

st.title("📊 Runs Dashboard")
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

st.markdown(f"### {len(runs)} Test Runs")

# Build runs summary table
rows = []
for run in runs:
    run_info = parse_run_name(run.get("run_name", "Unknown"))
    metrics = run.get("metrics", {})

    rows.append({
        "Run Name": run_info["display_name"],
        "Date": run_info["timestamp_str"],
        "TCs Run": run.get("test_cases_run", 0),
        "GT Expected": metrics.get("expected", 0),
        "API Found": metrics.get("found", 0),
        "TP": metrics.get("tp", 0),
        "FP": metrics.get("fp", 0),
        "FN": metrics.get("fn", 0),
        "Precision": f"{metrics.get('precision', 0):.1%}" if metrics.get('precision') else "0%",
        "Recall": f"{metrics.get('recall', 0):.1%}" if metrics.get('recall') else "0%",
    })

df = pd.DataFrame(rows)

# Display summary table
st.dataframe(
    df,
    use_container_width=True,
    hide_index=True,
)

st.divider()

# Confusion Matrix Section
st.markdown("### Confusion Matrix by Run")

# Create tabs for each run
if len(runs) > 0:
    tab_names = [parse_run_name(run.get("run_name", "Unknown"))["display_name"] for run in runs]
    tabs = st.tabs(tab_names[:10])  # Limit to 10 most recent runs

    for idx, (tab, run) in enumerate(zip(tabs, runs[:10])):
        with tab:
            run_info = parse_run_name(run.get("run_name", "Unknown"))
            metrics = run.get("metrics", {})

            # Display run metadata
            col1, col2, col3 = st.columns(3)
            col1.metric("Run", run_info["display_name"])
            col2.metric("Date", run_info["timestamp_str"])
            col3.metric("TCs Run", run.get("test_cases_run", 0))

            st.markdown("---")

            # Confusion Matrix
            tp = metrics.get("tp", 0)
            fp = metrics.get("fp", 0)
            fn = metrics.get("fn", 0)
            tn = 0  # Not applicable for GT comparison

            # Display confusion matrix
            st.markdown("#### Confusion Matrix")

            col_left, col_right = st.columns([2, 1])

            with col_left:
                # Create confusion matrix as a styled dataframe
                cm_data = {
                    "Predicted Positive": [tp, fn],
                    "Predicted Negative": [fp, tn if tn else "N/A"],
                }
                cm_df = pd.DataFrame(cm_data, index=["Actual Positive", "Actual Negative"])

                st.dataframe(
                    cm_df.style.apply(lambda x: ['background-color: #90EE90' if v == tp else
                                                   'background-color: #FFB6C6' if v in [fp, fn] else ''
                                                   for v in x], axis=1),
                    use_container_width=True,
                )

            with col_right:
                st.markdown("**Metrics:**")
                st.metric("True Positives (TP)", tp, help="Correctly identified GT findings")
                st.metric("False Positives (FP)", fp, help="API found but not in GT")
                st.metric("False Negatives (FN)", fn, help="In GT but API missed")

                # Derived metrics
                precision = metrics.get("precision", 0)
                recall = metrics.get("recall", 0)
                f1 = metrics.get("f1", 0)

                if precision or recall or f1:
                    st.markdown("---")
                    st.metric("Precision", f"{precision:.1%}")
                    st.metric("Recall", f"{recall:.1%}")
                    st.metric("F1 Score", f"{f1:.1%}")

            # Per-TC breakdown
            if run.get("per_tc_metrics"):
                st.markdown("---")
                st.markdown("#### Per Test Case Breakdown")

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
                    st.dataframe(tc_df, use_container_width=True, hide_index=True)

# Comparison section
st.divider()
st.markdown("### Run Comparison")

if len(runs) >= 2:
    # Allow user to select runs to compare
    run_names = [parse_run_name(r.get("run_name", "Unknown"))["display_name"] for r in runs]

    col1, col2 = st.columns(2)
    with col1:
        run1_name = st.selectbox("Select Run 1", run_names, key="run1")
    with col2:
        run2_name = st.selectbox("Select Run 2", run_names[1:] if len(run_names) > 1 else run_names, key="run2")

    # Find selected runs
    run1 = next((r for r in runs if parse_run_name(r.get("run_name", ""))["display_name"] == run1_name), None)
    run2 = next((r for r in runs if parse_run_name(r.get("run_name", ""))["display_name"] == run2_name), None)

    if run1 and run2:
        m1 = run1.get("metrics", {})
        m2 = run2.get("metrics", {})

        # Comparison metrics
        st.markdown("#### Side-by-Side Comparison")

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
if st.button("🔄 Refresh Data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
