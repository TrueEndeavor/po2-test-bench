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

from modules.db import get_test_documents_collection, get_golden_outputs_collection
from modules.naming import tc_sort_key, short_name
from modules.config import TEST_DOCS_DIR
from modules.api import submit_from_mongo, submit_document
from modules.parsers import parse_findings_summary
from modules.components import render_metrics_bar, render_tc_buttons, render_drilldown_panel


# ---------------------------------------------------------------------------
# Load test documents from MongoDB (fallback to local files)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600)
def load_test_docs():
    coll = get_test_documents_collection()
    return list(coll.find().sort("filename", 1))


test_docs = load_test_docs()
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

results = st.session_state["results"]

# ---------------------------------------------------------------------------
# Sidebar: Golden Baseline controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Golden Baseline")

    golden_coll = get_golden_outputs_collection()
    existing_count = golden_coll.count_documents({})
    st.metric("Golden Records", existing_count)

    run_label = st.text_input(
        "Run Label",
        value=f"baseline_{datetime.now().strftime('%Y%m%d')}",
    )

    capture = st.button(
        "Capture Golden Baseline",
        type="primary",
        use_container_width=True,
        help="Run all TCs and save outputs as golden reference data",
    )

    if existing_count > 0:
        st.divider()
        if st.button("Clear Golden Data", use_container_width=True):
            golden_coll.delete_many({})
            st.success("Cleared all golden records.")
            st.rerun()

# ---------------------------------------------------------------------------
# Baseline capture mode
# ---------------------------------------------------------------------------
if capture:
    st.title("PO2 Test Bench")
    st.subheader("Capturing Golden Baseline...")

    progress = st.progress(0, text="Starting baseline capture...")
    log = st.container()

    succeeded = 0
    failed = 0

    for i, item in enumerate(items):
        name = item["filename"] if use_mongo else item.name
        tc, desc = short_name(name)
        progress.progress(
            (i + 1) / doc_count,
            text=f"Running {tc} — {desc} ({i + 1}/{doc_count})",
        )

        try:
            if use_mongo:
                resp, meta = submit_from_mongo(item)
            else:
                resp, meta = submit_document(item)

            if resp.status_code == 200:
                full_response = json.loads(resp.text)
                findings = parse_findings_summary(resp.text)

                golden_coll.insert_one({
                    "filename": name,
                    "tc_number": tc,
                    "run_label": run_label,
                    "api_response": full_response,
                    "findings_summary": findings,
                    "total_findings": sum(findings.values()),
                    "status_code": resp.status_code,
                    "metadata": meta,
                    "created_at": datetime.utcnow(),
                })
                succeeded += 1
                log.success(f"{tc} — {sum(findings.values())} findings captured")
            else:
                failed += 1
                log.error(f"{tc} — HTTP {resp.status_code}")
        except Exception as e:
            failed += 1
            log.error(f"{tc} — {str(e)[:200]}")

    progress.progress(1.0, text="Baseline capture complete!")
    st.success(f"Done! {succeeded} succeeded, {failed} failed out of {doc_count} test cases.")

# ---------------------------------------------------------------------------
# Normal mode: TC runner + dashboard
# ---------------------------------------------------------------------------
else:
    st.title("PO2 Test Bench")
    render_metrics_bar(doc_count, results)
    st.divider()

    col_left, col_right = st.columns([1, 3])

    with col_left:
        render_tc_buttons(items, results, use_mongo)

    with col_right:
        render_drilldown_panel(results, items, use_mongo)
