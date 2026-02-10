"""
Add missing Ground Truth findings to golden_v1 baseline.

This script compares ground_truth.csv against captured findings in golden_v1
and adds any missing GT findings to the database.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Load Ground Truth CSV
# ---------------------------------------------------------------------------
GROUND_TRUTH_CSV = Path(__file__).resolve().parent / "ground_truth.csv"

if not GROUND_TRUTH_CSV.exists():
    print(f"❌ Ground truth CSV not found: {GROUND_TRUTH_CSV}")
    sys.exit(1)

gt_df = pd.read_csv(GROUND_TRUTH_CSV)
gt_df.columns = gt_df.columns.str.strip()

print(f"📋 Loaded {len(gt_df)} ground truth findings from CSV")

# ---------------------------------------------------------------------------
# Connect to MongoDB
# ---------------------------------------------------------------------------
uri = os.getenv("MONGODB_URI")
if not uri:
    with open(".env") as f:
        for line in f:
            if line.startswith("MONGODB_URI="):
                uri = line.split("=", 1)[1].strip()
                break

client = MongoClient(uri)
db = client["PO2xNW"]
golden_coll = db["golden_outputs"]

# ---------------------------------------------------------------------------
# Check each GT finding against golden_v1
# ---------------------------------------------------------------------------
run_label = "golden_v1"
golden_docs = list(golden_coll.find({"run_label": run_label}))

print(f"🔍 Checking {len(golden_docs)} documents in {run_label}")

missing_gt = []

for _, gt_row in gt_df.iterrows():
    tc_id = str(gt_row.get("TC Id", "")).strip()
    page = int(gt_row.get("Page Number", 0)) if pd.notna(gt_row.get("Page Number")) else 0
    sentence = str(gt_row.get("Non compliant", "")).strip()
    category = str(gt_row.get("Category", "")).strip()

    if not tc_id or not sentence:
        continue

    # Find matching document for this TC
    matching_doc = None
    for doc in golden_docs:
        if doc.get("tc_number", "").strip().upper() == tc_id.upper():
            matching_doc = doc
            break

    if not matching_doc:
        print(f"⚠️  No document found for TC {tc_id} in {run_label}")
        missing_gt.append({
            "tc_id": tc_id,
            "page": page,
            "sentence": sentence,
            "category": category,
            "reason": "TC not captured"
        })
        continue

    # Check if this GT finding exists in the document
    found = False
    api_response = matching_doc.get("api_response", {})

    for source_key in ["raw_output", "sequential_reasoner"]:
        source_data = api_response.get(source_key, {})
        if not isinstance(source_data, dict):
            continue

        for art_key, art_data in source_data.items():
            if not isinstance(art_data, dict):
                continue

            sections = art_data.get("sections", [])
            for section in sections:
                sec_page = section.get("page", 0)
                sec_sentence = str(section.get("sentence", "")).strip()

                # Match by page and sentence prefix (first 50 chars)
                if sec_page == page and sec_sentence[:50].lower() == sentence[:50].lower():
                    found = True
                    break

            if found:
                break

        if found:
            break

    if not found:
        missing_gt.append({
            "tc_id": tc_id,
            "page": page,
            "sentence": sentence,
            "category": category,
            "reason": "API missed this finding",
            "doc": matching_doc
        })

# ---------------------------------------------------------------------------
# Report missing GT findings
# ---------------------------------------------------------------------------
print(f"\n{'='*70}")
print(f"📊 MISSING GROUND TRUTH FINDINGS: {len(missing_gt)}")
print(f"{'='*70}\n")

if not missing_gt:
    print("✅ All ground truth findings are present in golden_v1!")
    client.close()
    sys.exit(0)

for i, mg in enumerate(missing_gt, 1):
    print(f"{i}. {mg['tc_id']} | Page {mg['page']} | {mg['category']}")
    print(f"   {mg['sentence'][:80]}...")
    print(f"   Reason: {mg['reason']}\n")

# ---------------------------------------------------------------------------
# Auto-add missing findings (no user prompt for automation)
# ---------------------------------------------------------------------------
print(f"{'='*70}")
print(f"\n➡️  Adding {len(missing_gt)} missing GT findings to {run_label}...\n")

# ---------------------------------------------------------------------------
# Add missing GT findings to database
# ---------------------------------------------------------------------------
added_count = 0

for mg in missing_gt:
    if "doc" not in mg:
        print(f"⚠️  Skipping {mg['tc_id']} - no base document found")
        continue

    doc = mg["doc"]

    # Create a new section entry for the missing GT finding
    new_section = {
        "page": mg["page"],
        "sentence": mg["sentence"],
        "category": mg["category"],
        "sub_bucket": "Ground Truth - Manually Added",
        "rule_citation": "Ground Truth Entry",
        "observations": "This finding was manually validated as ground truth but missed by the API.",
        "recommendations": "Review and validate.",
        "summary": f"Ground truth finding from manual validation.",
        "accept": True,
        "reject": False,
        "accept_with_changes": False,
    }

    # Add to raw_output -> first available artifact type
    api_response = doc.get("api_response", {})
    if "raw_output" not in api_response:
        api_response["raw_output"] = {}

    raw_output = api_response["raw_output"]

    # Find or create an artifact to add this to
    artifact_key = "misleading_artifact"  # Default for T1

    if artifact_key not in raw_output:
        raw_output[artifact_key] = {"sections": []}

    raw_output[artifact_key]["sections"].append(new_section)

    # Update the document in database
    result = golden_coll.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "api_response": api_response,
                "updated_at": datetime.utcnow(),
                "gt_additions": True  # Flag to indicate GT was added
            }
        }
    )

    if result.modified_count > 0:
        print(f"✅ Added GT finding to {mg['tc_id']} (page {mg['page']})")
        added_count += 1
    else:
        print(f"❌ Failed to add GT finding to {mg['tc_id']}")

print(f"\n{'='*70}")
print(f"✅ Successfully added {added_count} ground truth findings to {run_label}")
print(f"{'='*70}\n")

# ---------------------------------------------------------------------------
# Mark existing GT findings in database with is_gt flag
# ---------------------------------------------------------------------------
print("🔖 Marking ground truth findings in database...")

marked_count = 0

for _, gt_row in gt_df.iterrows():
    tc_id = str(gt_row.get("TC Id", "")).strip()
    page = int(gt_row.get("Page Number", 0)) if pd.notna(gt_row.get("Page Number")) else 0
    sentence = str(gt_row.get("Non compliant", "")).strip()

    if not tc_id or not sentence:
        continue

    # Find and mark this finding in all documents
    docs = list(golden_coll.find({
        "tc_number": {"$regex": f"^{tc_id}$", "$options": "i"}
    }))

    for doc in docs:
        api_response = doc.get("api_response", {})
        modified = False

        for source_key in ["raw_output", "sequential_reasoner"]:
            source_data = api_response.get(source_key, {})
            if not isinstance(source_data, dict):
                continue

            for art_key, art_data in source_data.items():
                if not isinstance(art_data, dict):
                    continue

                sections = art_data.get("sections", [])
                for section in sections:
                    sec_page = section.get("page", 0)
                    sec_sentence = str(section.get("sentence", "")).strip()

                    # Match by page and sentence prefix
                    if sec_page == page and sec_sentence[:50].lower() == sentence[:50].lower():
                        section["is_gt"] = True
                        section["gt_validated"] = True
                        modified = True

        if modified:
            golden_coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {"api_response": api_response}}
            )
            marked_count += 1

print(f"✅ Marked {marked_count} existing findings as ground truth in database\n")

print(f"{'='*70}")
print(f"🎉 COMPLETE!")
print(f"   • Added {added_count} missing GT findings")
print(f"   • Marked {marked_count} existing findings as GT")
print(f"   • Total GT findings in database: {marked_count + added_count}")
print(f"{'='*70}")

client.close()
