"""
Remove duplicate Ground Truth entries that were manually added but also exist in API output.

This script:
1. Finds all manually-added GT sections (sub_bucket="Ground Truth - Manually Added")
2. Checks if the same finding exists elsewhere in the document with is_gt=True
3. Removes the manual addition if a duplicate exists
"""
import os
import sys
from pathlib import Path

from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

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
# Find and remove duplicate manually-added GT entries
# ---------------------------------------------------------------------------
run_label = "golden_v1"
docs = list(golden_coll.find({"run_label": run_label, "gt_additions": True}))

print(f"🔍 Checking {len(docs)} documents in {run_label} for duplicate GT entries...\n")

total_removed = 0

for doc in docs:
    tc_number = doc.get("tc_number", "")
    api_response = doc.get("api_response", {})
    modified = False

    # Collect all manually-added GT sections
    manual_sections = []

    for source_key in ["raw_output", "sequential_reasoner"]:
        source_data = api_response.get(source_key, {})
        if not isinstance(source_data, dict):
            continue

        for art_key, art_data in source_data.items():
            if not isinstance(art_data, dict):
                continue

            sections = art_data.get("sections", [])
            for idx, section in enumerate(sections):
                sub_bucket = section.get("sub_bucket", "")
                if sub_bucket == "Ground Truth - Manually Added":
                    manual_sections.append({
                        "source": source_key,
                        "artifact": art_key,
                        "index": idx,
                        "page": section.get("page", 0),
                        "sentence": str(section.get("sentence", "")).strip().lower()[:50],
                        "section": section
                    })

    if not manual_sections:
        continue

    # For each manually-added section, check if duplicate exists elsewhere
    sections_to_remove = []

    for manual in manual_sections:
        found_duplicate = False

        # Search for matching GT-flagged section in the same document
        for source_key in ["raw_output", "sequential_reasoner"]:
            source_data = api_response.get(source_key, {})
            if not isinstance(source_data, dict):
                continue

            for art_key, art_data in source_data.items():
                if not isinstance(art_data, dict):
                    continue

                sections = art_data.get("sections", [])
                for idx, section in enumerate(sections):
                    # Skip the manual section itself
                    if (source_key == manual["source"] and
                        art_key == manual["artifact"] and
                        idx == manual["index"]):
                        continue

                    # Check if this is a GT-flagged duplicate
                    if section.get("is_gt", False) or section.get("gt_validated", False):
                        sec_page = section.get("page", 0)
                        sec_sentence = str(section.get("sentence", "")).strip().lower()[:50]

                        if sec_page == manual["page"] and sec_sentence == manual["sentence"]:
                            found_duplicate = True
                            break

                if found_duplicate:
                    break

            if found_duplicate:
                break

        if found_duplicate:
            sections_to_remove.append(manual)

    # Remove duplicate manual sections (sort by index descending to avoid index shifts)
    if sections_to_remove:
        # Group by source/artifact and sort indices descending
        removals_by_artifact = {}
        for manual in sections_to_remove:
            key = (manual["source"], manual["artifact"])
            if key not in removals_by_artifact:
                removals_by_artifact[key] = []
            removals_by_artifact[key].append(manual)

        # Remove in descending index order within each artifact
        for (source_key, art_key), manuals in removals_by_artifact.items():
            manuals_sorted = sorted(manuals, key=lambda m: m["index"], reverse=True)

            source_data = api_response[source_key]
            art_data = source_data[art_key]
            sections = art_data["sections"]

            for manual in manuals_sorted:
                removed_section = sections.pop(manual["index"])
                modified = True
                total_removed += 1

                print(f"✓ Removed duplicate manual GT from {tc_number}")
                print(f"  Page {removed_section.get('page', 0)}: {removed_section.get('sentence', '')[:60]}...")
                print()

    # Update document if modified
    if modified:
        golden_coll.update_one(
            {"_id": doc["_id"]},
            {"$set": {"api_response": api_response}}
        )

print(f"{'='*70}")
print(f"✅ Removed {total_removed} duplicate manually-added GT entries")
print(f"{'='*70}")

client.close()
