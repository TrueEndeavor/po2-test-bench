"""Run missing TCs for silver_v1 golden baseline capture using local PDF files."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

from modules.api import submit_document
from modules.parsers import parse_findings_summary
from modules.naming import tc_sort_key, short_name
from modules.config import TEST_DOCS_DIR

MONGODB_URI = os.getenv("MONGODB_URI")
client = MongoClient(MONGODB_URI)
db = client["PO2xNW"]
golden_coll = db["golden_outputs"]

RUN_LABEL = "silver_v1"

# Find which TCs already have silver_v1
existing_tcs = set(golden_coll.distinct("tc_number", {"run_label": RUN_LABEL}))
print(f"Existing silver_v1 TCs: {sorted(existing_tcs)}")

# Load local PDF files
pdf_files = sorted(TEST_DOCS_DIR.glob("*.pdf"), key=lambda x: tc_sort_key(x.name))
print(f"Total local PDFs: {len(pdf_files)}")

# Filter to missing TCs
missing = []
for pdf in pdf_files:
    tc, desc = short_name(pdf.name)
    if tc not in existing_tcs:
        missing.append((pdf, tc, desc))

print(f"Missing TCs to run: {[tc for _, tc, _ in missing]}")

if not missing:
    print("All TCs already captured! Nothing to do.")
    sys.exit(0)

# Run each missing TC
succeeded = 0
failed = 0

for pdf, tc, desc in missing:
    print(f"\n--- Running {tc} — {desc} ({pdf.name}) ---")
    try:
        resp, meta = submit_document(pdf)

        if resp.status_code == 200:
            full_response = json.loads(resp.text)
            findings = parse_findings_summary(resp.text)

            golden_coll.insert_one({
                "filename": pdf.name,
                "tc_number": tc,
                "run_label": RUN_LABEL,
                "api_response": full_response,
                "findings_summary": findings,
                "total_findings": sum(findings.values()),
                "status_code": resp.status_code,
                "metadata": meta,
                "created_at": datetime.utcnow(),
            })
            succeeded += 1
            print(f"  OK — {sum(findings.values())} findings captured")
        else:
            failed += 1
            print(f"  FAILED — HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        failed += 1
        print(f"  ERROR — {str(e)[:300]}")

print(f"\n=== Done! {succeeded} succeeded, {failed} failed out of {len(missing)} ===")
