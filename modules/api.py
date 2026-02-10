import os
import json
import requests
from datetime import datetime, timezone
from modules.config import API_URL
from modules.naming import guess_doc_type


def build_metadata(filename):
    """Build metadata in the flat format the API expects."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "event_details": {
            "event_name": "Compliance Review Initiated",
            "timestamp": now,
            "initiating_user_id": "nw-testing-team",
            "source_system": "Red Oak",
        },
        "document_metadata": {
            "document_id": f"REG-{os.urandom(4).hex().upper()}",
            "document_name": filename.replace(".pdf", ""),
            "document_type": guess_doc_type(filename),
            "file_format": "PDF",
        },
        "compliance_context": {
            "audience_classification": "Retail",
            "product_program_identifiers": "General",
            "regulatory_frameworks": ["SEC", "FINRA"],
            "material_classification": "New Content",
            "update_frequency": "Quarterly",
        },
    }


def submit_from_mongo(doc):
    """POST a document loaded from MongoDB test_documents collection."""
    filename = doc["filename"]
    metadata = build_metadata(filename)
    file_bytes = bytes(doc["file_data"])
    files = {"file": (filename, file_bytes, "application/pdf")}
    data = {"metadata": json.dumps(metadata)}
    response = requests.post(API_URL, files=files, data=data, timeout=120)
    return response, metadata


def submit_document(filepath):
    """POST a document from a local file path."""
    filename = filepath.name
    metadata = build_metadata(filename)
    with open(filepath, "rb") as f:
        files = {"file": (filename, f, "application/pdf")}
        data = {"metadata": json.dumps(metadata)}
        response = requests.post(API_URL, files=files, data=data, timeout=120)
    return response, metadata
