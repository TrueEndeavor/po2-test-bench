import os
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
from bson import ObjectId
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="PO2 Test Bench",
    page_icon="\U0001f50d",
    layout="wide",
)

# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------
@st.cache_resource
def get_mongo_client():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        st.error("MONGODB_URI not found in .env")
        st.stop()
    return MongoClient(uri)


def get_collection():
    client = get_mongo_client()
    return client["PO2xNW"]["PO2_testing"]


ARTIFACT_TYPES = [
    "misleading_artifact", "performance_artifact", "disclosure_artifact",
    "testimonial_artifact", "digital_artifact", "comparison_artifact",
    "ranking_artifact", "thirdparty_artifact", "editorial_artifact",
    "typo_artifact",
]

REVIEW_FIELDS = ["accept", "accept_with_changes", "reject", "reject_reason"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def fetch_docs_by_date(date_val):
    coll = get_collection()
    start = datetime.combine(date_val, datetime.min.time())
    end = start + timedelta(days=1)
    return list(coll.find({"created_at": {"$gte": start, "$lt": end}}))


def fetch_all_docs():
    coll = get_collection()
    return list(coll.find().sort("created_at", -1))


def extract_findings(doc, source="sequential_reasoner"):
    """Extract findings with MongoDB path metadata for write-back."""
    doc_id = doc["_id"]
    data = doc.get(source, {})
    actual_source = source
    if not data or not isinstance(data, dict):
        data = doc.get("raw_output", {})
        actual_source = "raw_output"

    rows = []
    for art_key in ARTIFACT_TYPES:
        art = data.get(art_key, {})
        if not isinstance(art, dict):
            continue
        for idx, s in enumerate(art.get("sections", [])):
            rows.append({
                # --- path metadata (for DB write-back) ---
                "_doc_id": str(doc_id),
                "_source": actual_source,
                "_art_key": art_key,
                "_section_idx": idx,
                # --- display fields ---
                "artifact_type": art_key.replace("_artifact", ""),
                "sentence": s.get("sentence", ""),
                "page": s.get("page_number", ""),
                "rule_citation": s.get("rule_citation", ""),
                "recommendations": s.get("recommendations", ""),
                "category": s.get("category", "N/A"),
                "observations": s.get("observations", ""),
                "summary": s.get("summary", ""),
                # --- editable review fields ---
                "accept": bool(s.get("accept", False)),
                "accept_with_changes": bool(s.get("accept_with_changes", False)),
                "reject": bool(s.get("reject", False)),
                "reject_reason": s.get("reject_reason", "") or "",
            })
    return rows


# ---------------------------------------------------------------------------
# MongoDB write-back
# ---------------------------------------------------------------------------
def save_finding(row):
    """Persist a single finding's review fields back to MongoDB."""
    coll = get_collection()
    doc_id = ObjectId(row["_doc_id"])
    base_path = f"{row['_source']}.{row['_art_key']}.sections.{row['_section_idx']}"

    update = {}
    for field in REVIEW_FIELDS:
        update[f"{base_path}.{field}"] = row[field]

    coll.update_one({"_id": doc_id}, {"$set": update})


def save_all_changes(original_df, edited_df):
    """Compare original vs edited, persist only changed rows. Returns count."""
    changed = 0
    for idx in edited_df.index:
        if idx not in original_df.index:
            continue
        orig = original_df.loc[idx]
        edit = edited_df.loc[idx]
        # Check if any review field changed
        diff = False
        for f in REVIEW_FIELDS:
            if orig[f] != edit[f]:
                diff = True
                break
        if diff:
            save_finding(edit)
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Sidebar — Filters
# ---------------------------------------------------------------------------
st.sidebar.title("Filters")

review_date = st.sidebar.date_input("Review Date", value=datetime.now().date())
fetch_clicked = st.sidebar.button("Fetch Records", type="primary", use_container_width=True)

if fetch_clicked:
    docs = fetch_docs_by_date(review_date)
    if not docs:
        docs = fetch_all_docs()
        st.sidebar.warning(f"No records for {review_date}. Showing all records.")
    st.session_state["docs"] = docs
    st.session_state["fetch_date"] = review_date
    # Clear stale editor state when new docs are fetched
    for key in list(st.session_state.keys()):
        if key.startswith("editor_"):
            del st.session_state[key]

if "docs" not in st.session_state:
    st.session_state["docs"] = fetch_all_docs()
    st.session_state["fetch_date"] = None

docs = st.session_state["docs"]

if not docs:
    st.title("PO2 Test Bench — Record Viewer")
    st.info("No records found. Try a different date.")
    st.stop()

# Build doc options
doc_options = {}
for d in docs:
    meta = d.get("metadata", {})
    doc_meta = meta.get("others", {}).get("document_metadata", {})
    name = doc_meta.get("document_name", "Unknown")
    created = d.get("created_at")
    ts = ""
    if created:
        try:
            ts = created.strftime("%Y-%m-%d %H:%M")
        except Exception:
            ts = str(created)[:16]
    label = f"{name} ({ts})" if ts else name
    doc_options[str(d["_id"])] = {"label": label, "doc": d}

selected_id = st.sidebar.selectbox(
    "Review Record",
    options=list(doc_options.keys()),
    format_func=lambda x: doc_options[x]["label"],
)

source = st.sidebar.radio("Data Source", ["sequential_reasoner", "raw_output"], horizontal=True)

st.sidebar.divider()
st.sidebar.caption("Edits to Accept / Reject auto-save to the database.")

# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------
st.title("PO2 Test Bench — Record Viewer")

selected_doc = doc_options[selected_id]["doc"]
findings = extract_findings(selected_doc, source=source)

if not findings:
    st.warning("No findings in this record.")
    st.stop()

df = pd.DataFrame(findings)

# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------
total = len(df)
accepted = int(df["accept"].sum())
rejected = int(df["reject"].sum())
changed = int(df["accept_with_changes"].sum())
unreviewed = total - accepted - rejected - changed

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Findings", total)
c2.metric("Accepted", accepted)
c3.metric("Accept w/ Changes", changed)
c4.metric("Rejected", rejected)
c5.metric("Unreviewed", unreviewed)

st.divider()

# ---------------------------------------------------------------------------
# Editable findings table
# ---------------------------------------------------------------------------
st.subheader("Compliance Findings")
st.caption("Edit the checkboxes and reject reason directly in the table, then click **Save Changes**.")

# Columns to show in the editor (hide path metadata)
visible_cols = [
    "sentence", "rule_citation", "recommendations", "category",
    "accept", "accept_with_changes", "reject", "reject_reason",
]

editor_key = f"editor_{selected_id}_{source}"

edited_df = st.data_editor(
    df[visible_cols],
    use_container_width=True,
    hide_index=True,
    height=500,
    num_rows="fixed",
    key=editor_key,
    column_config={
        "sentence": st.column_config.TextColumn("Flagged Text", width="large", disabled=True),
        "rule_citation": st.column_config.TextColumn("Rule Citation", width="medium", disabled=True),
        "recommendations": st.column_config.TextColumn("Recommendations", width="large", disabled=True),
        "category": st.column_config.TextColumn("Category", width="medium", disabled=True),
        "accept": st.column_config.CheckboxColumn("Accept", width="small"),
        "accept_with_changes": st.column_config.CheckboxColumn("Accept w/ Changes", width="small"),
        "reject": st.column_config.CheckboxColumn("Reject", width="small"),
        "reject_reason": st.column_config.TextColumn("Reject Reason", width="medium"),
    },
)

# ---------------------------------------------------------------------------
# Save button
# ---------------------------------------------------------------------------
col_save, col_status = st.columns([1, 3])

with col_save:
    if st.button("Save Changes", type="primary", use_container_width=True):
        # Merge edited review columns back with path metadata
        save_df = df.copy()
        for f in REVIEW_FIELDS:
            save_df[f] = edited_df[f].values

        count = save_all_changes(df, save_df)
        if count > 0:
            st.session_state["last_save"] = f"Saved {count} finding(s) to database."
            # Refresh the doc from DB so the page reflects saved state
            refreshed = get_collection().find_one({"_id": ObjectId(selected_id)})
            if refreshed:
                doc_options[selected_id]["doc"] = refreshed
                idx = next(i for i, d in enumerate(st.session_state["docs"]) if str(d["_id"]) == selected_id)
                st.session_state["docs"][idx] = refreshed
            st.rerun()
        else:
            st.session_state["last_save"] = "No changes detected."
            st.rerun()

with col_status:
    if "last_save" in st.session_state:
        st.success(st.session_state.pop("last_save"))

# ---------------------------------------------------------------------------
# Detailed view (expandable per finding)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Finding Details")

for i, row in df.iterrows():
    status = (
        "Accepted" if row["accept"]
        else "Rejected" if row["reject"]
        else "Accept w/ Changes" if row["accept_with_changes"]
        else "Unreviewed"
    )
    color = {"Accepted": "green", "Rejected": "red", "Accept w/ Changes": "orange", "Unreviewed": "gray"}[status]
    sentence_preview = row["sentence"][:80] + "..." if len(str(row["sentence"])) > 80 else row["sentence"]
    label = f":{color}[**{status}**] {row['category']} — {sentence_preview}"

    with st.expander(label, expanded=False):
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"**Artifact Type:** {row['artifact_type']}")
            st.markdown(f"**Page:** {row['page']}")
            st.markdown(f"**Category:** {row['category']}")
            st.markdown(f"**Rule Citation:** {row['rule_citation']}")
        with col_b:
            st.markdown(f"**Observations:** {row['observations']}")
            st.markdown(f"**Recommendations:** {row['recommendations']}")
            st.markdown(f"**Summary:** {row['summary']}")
            if row["reject_reason"]:
                st.markdown(f"**Reject Reason:** :red[{row['reject_reason']}]")
