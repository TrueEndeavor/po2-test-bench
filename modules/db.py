import os
import streamlit as st
from pymongo import MongoClient
from bson import ObjectId
from modules.config import REVIEW_FIELDS


@st.cache_resource
def get_client():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        st.error("MONGODB_URI not found in .env")
        st.stop()
    return MongoClient(uri)


def get_db():
    return get_client()["PO2xNW"]


def get_results_collection():
    return get_db()["PO2_testing"]


def get_test_documents_collection():
    return get_db()["test_documents"]


def get_golden_outputs_collection():
    return get_db()["golden_outputs"]


def save_finding(row):
    """Persist a single finding's review fields back to MongoDB."""
    coll = get_results_collection()
    doc_id = ObjectId(row["_doc_id"])
    base_path = f"{row['_source']}.{row['_art_key']}.sections.{row['_section_idx']}"
    update = {f"{base_path}.{field}": row[field] for field in REVIEW_FIELDS}
    coll.update_one({"_id": doc_id}, {"$set": update})


def save_all_changes(original_df, edited_df):
    """Compare original vs edited, persist only changed rows. Returns count."""
    changed = 0
    for idx in edited_df.index:
        if idx not in original_df.index:
            continue
        orig = original_df.loc[idx]
        edit = edited_df.loc[idx]
        if any(orig[f] != edit[f] for f in REVIEW_FIELDS):
            save_finding(edit)
            changed += 1
    return changed
