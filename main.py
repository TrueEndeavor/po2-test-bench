import json
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="PO2 Compliance Test Runner",
    page_icon="\U0001f9ea",
    layout="wide",
)

from modules.db import get_test_documents_collection
from modules.naming import tc_sort_key, short_name
from modules.config import TEST_DOCS_DIR
from modules.components import render_tc_buttons, render_drilldown_panel
from modules.ground_truth import load_ground_truth
from modules.run_names import generate_run_name, parse_run_name
from modules.db import save_run


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
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f"### \U0001f3af {run_info['display_name']}")
    st.caption(run_info["timestamp_str"])

    prompt_label = st.text_input(
        "Prompt Version", placeholder="e.g. v1.2.2",
        key="prompt_label",
    )
    run_by = st.text_input(
        "Run By", placeholder="e.g. Latha",
        key="run_by",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Save", use_container_width=True):
            if results:
                save_run(run_name, results, prompt_label, run_by)
                st.success("Saved!")
            else:
                st.warning("No results yet.")
    with c2:
        if st.button("New Run", use_container_width=True):
            if results:
                save_run(run_name, results, prompt_label, run_by)
            st.session_state["run_name"] = generate_run_name()
            st.session_state["results"] = {}
            st.session_state.pop("drill_level", None)
            st.session_state.pop("drill_theme", None)
            st.rerun()

    if not gt_df.empty:
        st.divider()
        st.caption(f"**GT:** {len(gt_df)} findings / {gt_df['TC Id'].nunique()} TCs")
    else:
        st.divider()
        st.warning("No ground truth CSV loaded")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("Compliance Test Runner")

render_drilldown_panel(results, items, use_mongo, gt_keys, gt_df)

st.divider()

render_tc_buttons(items, results, use_mongo, gt_keys, gt_df, run_name,
                  prompt_label=prompt_label, run_by=run_by)
