import os
import re
import json
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Capture Golden Baseline",
    page_icon="📦",
    layout="wide",
)

from modules.db import (
    get_golden_outputs_collection,
    get_test_documents_collection,
)
from modules.parsers import parse_findings_summary
from modules.naming import tc_sort_key, short_name
from modules.config import TEST_DOCS_DIR
from modules.api import submit_from_mongo, submit_document

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown("## 📦 Capture Golden Baseline")
st.caption(
    "Run all test cases through the API and save outputs as a golden baseline for curation. "
    "You can resume incomplete captures at any time."
)

st.divider()

# ---------------------------------------------------------------------------
# Capture Controls
# ---------------------------------------------------------------------------
golden_coll = get_golden_outputs_collection()
test_docs_coll = get_test_documents_collection()

# Auto-increment golden version
existing_labels = golden_coll.distinct("run_label")
golden_versions = [
    int(l.split("_v")[1])
    for l in existing_labels
    if l.startswith("golden_v") and l.split("_v")[1].isdigit()
]
next_version = max(golden_versions, default=0) + 1

# Show existing baselines
if existing_labels:
    with st.expander("📋 Existing Baselines", expanded=False):
        for label in sorted(existing_labels, reverse=True):
            count = golden_coll.count_documents({"run_label": label})
            st.caption(f"**{label}**: {count} test cases")

col_label, col_mode = st.columns([2, 1])

with col_label:
    new_run_label = st.text_input(
        "Baseline Label",
        value=f"golden_v{next_version}",
        help="Name for this golden baseline (e.g., golden_v1, golden_v2)",
        key="capture_run_label",
    )

with col_mode:
    resume_mode = st.checkbox(
        "Resume existing run",
        value=False,
        help="Only run TCs that are missing from this run_label (useful if you stopped mid-capture)",
    )

# Check what's already captured for this run_label
existing_tcs = set()
if resume_mode:
    existing_docs = list(golden_coll.find({"run_label": new_run_label}))
    existing_tcs = set(doc.get("tc_number", "") for doc in existing_docs)
    if existing_tcs:
        st.info(f"📋 Found **{len(existing_tcs)} TCs** already captured for **{new_run_label}**: {', '.join(sorted(existing_tcs, key=tc_sort_key))}")
    else:
        st.warning(f"⚠️ No existing TCs found for **{new_run_label}**. Will run all TCs.")

capture_btn = st.button(
    f"▶ {'Resume' if resume_mode else 'Start'} Capture",
    type="primary",
    use_container_width=True,
    key="capture_golden_btn",
)

# ---------------------------------------------------------------------------
# Capture Execution
# ---------------------------------------------------------------------------
if capture_btn:
    # Load test documents
    test_docs = list(test_docs_coll.find().sort("filename", 1))
    use_mongo = len(test_docs) > 0

    if not use_mongo:
        pdf_files = sorted(TEST_DOCS_DIR.glob("*.pdf"))
        if not pdf_files:
            st.error("No test documents found.")
            st.stop()
        items = sorted(pdf_files, key=lambda x: tc_sort_key(x.name))
    else:
        items = sorted(test_docs, key=lambda x: tc_sort_key(x["filename"]))

    # Filter out already captured TCs if in resume mode
    if resume_mode and existing_tcs:
        items = [
            item for item in items
            if short_name(item["filename"] if use_mongo else item.name)[0] not in existing_tcs
        ]

    doc_count = len(items)

    if doc_count == 0:
        st.success(f"✅ All TCs already captured for **{new_run_label}**! Nothing to do.")
        st.info("💡 Go to the **Golden Dataset Admin** page to review and curate findings.")
        st.stop()

    st.divider()
    if resume_mode and existing_tcs:
        st.subheader(f"Resuming {new_run_label}...")
        st.caption(f"✓ {len(existing_tcs)} already done  |  ⏳ {doc_count} remaining")
    else:
        st.subheader(f"Capturing {new_run_label}...")
        st.caption(f"Running {doc_count} test cases")

    # Create placeholders for real-time updates
    progress_bar = st.progress(0, text="Starting baseline capture...")
    status_text = st.empty()
    results_area = st.container()

    succeeded = 0
    failed = 0

    for i, item in enumerate(items):
        name = item["filename"] if use_mongo else item.name
        tc, desc = short_name(name)

        # Update status text
        status_text.info(f"⏳ Running **{tc}** — {desc} ({i + 1}/{doc_count})...")

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
                    "run_label": new_run_label,
                    "api_response": full_response,
                    "findings_summary": findings,
                    "total_findings": sum(findings.values()),
                    "status_code": resp.status_code,
                    "metadata": meta,
                    "created_at": datetime.utcnow(),
                })
                succeeded += 1

                # Show result immediately
                with results_area:
                    st.success(f"✓ **{tc}** — {sum(findings.values())} findings captured — {desc}")
            else:
                failed += 1
                with results_area:
                    st.error(f"✗ **{tc}** — HTTP {resp.status_code} — {desc}")
        except Exception as e:
            failed += 1
            with results_area:
                st.error(f"✗ **{tc}** — {str(e)[:100]} — {desc}")

        # Update progress bar after each TC
        progress_bar.progress(
            (i + 1) / doc_count,
            text=f"Progress: {i + 1}/{doc_count} completed ({succeeded} ✓, {failed} ✗)",
        )

    # Final status
    status_text.success(
        f"🎉 **Capture Complete!** {succeeded} succeeded, {failed} failed out of {doc_count} test cases."
    )

    st.balloons()

    col_next1, col_next2 = st.columns(2)
    with col_next1:
        if st.button("🔄 Capture Another Baseline", type="secondary", use_container_width=True):
            st.rerun()

    with col_next2:
        st.markdown(f"**Next:** Go to **Golden Dataset Admin** page to review **{new_run_label}**")
