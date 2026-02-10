import json
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="PO2 Test Bench",
    page_icon="\U0001f9ea",
    layout="wide",
)

from modules.db import get_test_documents_collection
from modules.naming import tc_sort_key, short_name
from modules.config import TEST_DOCS_DIR
from modules.api import submit_from_mongo, submit_document
from modules.parsers import parse_findings_summary, extract_findings_for_review
from modules.components import render_metrics_bar, render_tc_buttons, render_drilldown_panel
from modules.ground_truth import load_ground_truth, is_ground_truth, calculate_gt_metrics, get_missing_gt_findings
from modules.run_names import generate_run_name, parse_run_name


# ---------------------------------------------------------------------------
# Load test documents from MongoDB (fallback to local files)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600)
def load_test_docs():
    coll = get_test_documents_collection()
    return list(coll.find().sort("filename", 1))


@st.cache_data(ttl=600)
def load_gt_data():
    """Load ground truth keys and dataframe."""
    return load_ground_truth()


test_docs = load_test_docs()
gt_keys, gt_df = load_gt_data()
use_mongo = len(test_docs) > 0

if not use_mongo:
    pdf_files = sorted(TEST_DOCS_DIR.glob("*.pdf"))
    if not pdf_files:
        st.error("No test documents found.")
        st.stop()
    items = sorted(pdf_files, key=lambda x: tc_sort_key(x.name))
else:
    items = sorted(test_docs, key=lambda x: tc_sort_key(x["filename"]))

doc_count = len(items)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "results" not in st.session_state:
    st.session_state["results"] = {}

if "run_name" not in st.session_state:
    st.session_state["run_name"] = generate_run_name()

results = st.session_state["results"]
run_name = st.session_state["run_name"]
run_info = parse_run_name(run_name)

# ---------------------------------------------------------------------------
# Sidebar: Run Info & Ground Truth controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Current Run")
    st.subheader(f"🎯 {run_info['display_name']}")
    st.caption(f"Started: {run_info['timestamp_str']}")
    st.caption(f"ID: `{run_name}`")

    if st.button("🔄 Start New Run", use_container_width=True):
        st.session_state["run_name"] = generate_run_name()
        st.session_state["results"] = {}
        st.session_state.pop("drill_level", None)
        st.session_state.pop("drill_theme", None)
        st.rerun()

    st.divider()
    st.header("Ground Truth")

    if not gt_df.empty:
        total_gt = len(gt_df)
        unique_tcs = gt_df["TC Id"].nunique()
        st.metric("GT Findings", total_gt)
        st.caption(f"Across {unique_tcs} test cases")
    else:
        st.warning("No ground truth CSV loaded")

# ---------------------------------------------------------------------------
# Main: TC runner + dashboard
# ---------------------------------------------------------------------------
st.title("PO2 Test Bench")
render_metrics_bar(doc_count, results, gt_keys, gt_df)
st.divider()

col_left, col_right = st.columns([1, 3])

with col_left:
    render_tc_buttons(items, results, use_mongo, gt_keys, gt_df, run_name)

with col_right:
    render_drilldown_panel(results, items, use_mongo, gt_keys, gt_df)
