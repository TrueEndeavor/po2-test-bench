import os
from datetime import datetime

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


def get_golden_deletions_collection():
    return get_db()["golden_deletions"]


def get_golden_category_status_collection():
    return get_db()["golden_category_status"]


# ---------------------------------------------------------------------------
# simple_viewer.py write-back (PO2_testing collection)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Legacy physical delete (kept for backwards compat — NOT used by golden_admin)
# ---------------------------------------------------------------------------
def delete_golden_finding(doc_id, source_key, art_key, section_idx):
    """Remove a single finding (section) from a golden_outputs document."""
    coll = get_golden_outputs_collection()
    oid = ObjectId(doc_id)
    path = f"api_response.{source_key}.{art_key}.sections.{section_idx}"
    coll.update_one({"_id": oid}, {"$unset": {path: 1}})
    pull_path = f"api_response.{source_key}.{art_key}.sections"
    coll.update_one({"_id": oid}, {"$pull": {pull_path: None}})


def delete_golden_findings_batch(deletions):
    """Delete multiple findings, processing in reverse index order to avoid shift."""
    from collections import defaultdict
    groups = defaultdict(list)
    for d in deletions:
        key = (d["_doc_id"], d["_source"], d["_art_key"])
        groups[key].append(d["_section_idx"])
    count = 0
    for (doc_id, source, art_key), indices in groups.items():
        for idx in sorted(indices, reverse=True):
            delete_golden_finding(doc_id, source, art_key, idx)
            count += 1
    return count


def refresh_golden_summary(doc_id):
    """Recalculate findings_summary and total_findings after deletions."""
    from collections import Counter
    from modules.config import ARTIFACT_TYPES
    coll = get_golden_outputs_collection()
    doc = coll.find_one({"_id": ObjectId(doc_id)})
    if not doc:
        return
    categories = Counter()
    api_resp = doc.get("api_response", {})
    for source_key in ["raw_output", "sequential_reasoner"]:
        source_data = api_resp.get(source_key, {})
        if not isinstance(source_data, dict):
            continue
        for art_key in ARTIFACT_TYPES:
            art = source_data.get(art_key, {})
            if not isinstance(art, dict):
                continue
            for s in art.get("sections", []):
                cat = s.get("category", "Uncategorized")
                categories[cat] += 1
    coll.update_one({"_id": ObjectId(doc_id)}, {"$set": {
        "findings_summary": dict(categories),
        "total_findings": sum(categories.values()),
    }})


# ---------------------------------------------------------------------------
# Soft-delete: golden_deletions collection (non-destructive)
# ---------------------------------------------------------------------------
def soft_delete_finding(doc_id, source, art_key, section_idx,
                        category="", sub_bucket="", sentence=""):
    """Record a deletion in golden_deletions. Source doc is NEVER modified."""
    coll = get_golden_deletions_collection()
    coll.update_one(
        {
            "doc_id": doc_id,
            "source": source,
            "art_key": art_key,
            "section_idx": section_idx,
        },
        {"$set": {
            "doc_id": doc_id,
            "source": source,
            "art_key": art_key,
            "section_idx": section_idx,
            "category": category,
            "sub_bucket": sub_bucket,
            "sentence_preview": str(sentence)[:120],
            "deleted_at": datetime.utcnow(),
        }},
        upsert=True,
    )


def soft_delete_batch(deletions):
    """Soft-delete multiple findings. Returns count."""
    count = 0
    for d in deletions:
        soft_delete_finding(
            d["_doc_id"], d["_source"], d["_art_key"], d["_section_idx"],
            d.get("category", ""), d.get("sub_bucket", ""),
            d.get("sentence", ""),
        )
        count += 1
    return count


def undo_soft_delete(doc_id, source, art_key, section_idx):
    """Undo a single soft deletion."""
    coll = get_golden_deletions_collection()
    coll.delete_one({
        "doc_id": doc_id,
        "source": source,
        "art_key": art_key,
        "section_idx": section_idx,
    })


def load_deletion_keys():
    """Return set of (doc_id, source, art_key, section_idx) tuples for all deletions."""
    coll = get_golden_deletions_collection()
    return set(
        (d["doc_id"], d["source"], d["art_key"], d["section_idx"])
        for d in coll.find({}, {"doc_id": 1, "source": 1, "art_key": 1, "section_idx": 1})
    )


def get_deletion_count():
    """Return total number of soft-deleted findings."""
    return get_golden_deletions_collection().count_documents({})


# ---------------------------------------------------------------------------
# Category curation status: golden_category_status collection
# ---------------------------------------------------------------------------
def lock_category(category, findings_before, findings_after):
    """Lock a curated category — marks it as precious/immutable."""
    coll = get_golden_category_status_collection()
    coll.update_one(
        {"category": category},
        {"$set": {
            "category": category,
            "status": "locked",
            "locked_at": datetime.utcnow(),
            "findings_before": findings_before,
            "findings_after": findings_after,
            "deletions": findings_before - findings_after,
        }},
        upsert=True,
    )


def unlock_category(category):
    """Unlock a category for re-curation (admin only)."""
    coll = get_golden_category_status_collection()
    coll.update_one(
        {"category": category},
        {"$set": {"status": "unlocked", "unlocked_at": datetime.utcnow()}},
    )


def get_category_statuses():
    """Return dict of category -> status doc."""
    coll = get_golden_category_status_collection()
    return {d["category"]: d for d in coll.find()}
