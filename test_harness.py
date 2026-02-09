import os
import re
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st
from bson import ObjectId, Binary
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="PO2 Test Bench", page_icon="\U0001f9ea", layout="wide")

# API_URL = "http://34.63.177.131:8000/analyze"
API_BASE = "https://po2-api-dev.turboverse.co"
GCS_BUCKET = "gs://po2_documents/uploads"
AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "")
POLL_INTERVAL = 10  # seconds between status checks
POLL_TIMEOUT = 600  # max seconds to wait for result
TEST_DOCS_DIR = Path(__file__).resolve().parent / "test_docs"

DOC_TYPE_MAP = {
    "FS": "Fund Sheet",
    "PP": "Pitch Presentation",
    "CM": "Commentary",
    "BR": "Brochure",
    "RMCM": "Commentary",
}

ARTIFACT_TYPES = [
    "misleading_artifact", "performance_artifact", "disclosure_artifact",
    "testimonial_artifact", "digital_artifact", "comparison_artifact",
    "ranking_artifact", "thirdparty_artifact", "editorial_artifact",
    "typo_artifact",
]

# Category -> Theme mapping (display order)
CATEGORY_THEMES = {
    "Misleading or Unsubstantiated Claims":   "T1 - Misleading",
    "Performance Presentation & Reporting":   "T2 - Performance",
    "Inadequate or Missing Disclosures":      "T3 - Disclosures",
    "Testimonials & Endorsements":            "T4 - Testimonials",
    "Digital & Distribution Controls":        "T5 - Digital",
    "Comparisons and Competitive Claims":     "T6 - Comparisons",
    "Ratings & Data Context Validation":      "T7 - Rankings",
    "Improper Use of Third-Party Content & Intellectual Property": "T8 - Third-Party",
    "Editorial (Non-Regulatory)":             "T9 - Editorial",
}

# Reverse for sorting
THEME_ORDER = {v: i for i, v in enumerate(CATEGORY_THEMES.values())}


def cat_to_theme(cat):
    """Map a raw category name to its short theme label."""
    for key, theme in CATEGORY_THEMES.items():
        if key.lower() in cat.lower() or cat.lower() in key.lower():
            return theme
    # Fuzzy: match first significant word
    for key, theme in CATEGORY_THEMES.items():
        if key.split()[0].lower() in cat.lower():
            return theme
    return cat[:25]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_mongo():
    uri = os.getenv("MONGODB_URI")
    return MongoClient(uri)["PO2xNW"]


def tc_sort_key(filename):
    m = re.search(r"TC(\d+)", filename)
    return int(m.group(1)) if m else 999


def short_name(filename):
    """Extract TC number and short description."""
    name = filename.replace(".pdf", "")
    tc_match = re.search(r"(TC\d+)", name)
    tc = tc_match.group(1) if tc_match else "TC?"
    tc_num = re.search(r"\d+", tc)
    if tc_num:
        tc = f"TC{int(tc_num.group()):02d}"

    # Extract variant suffix like -1A, -1B, -1C before cleaning
    variant = ""
    v_match = re.search(r"[-_ ](1[A-C])\b", name)
    if v_match:
        variant = f" ({v_match.group(1)})"

    # "Copy of" prefix for duplicate TCs
    if "Copy of" in name:
        variant = " (alt)"

    # Get everything after TC##
    after_tc = re.split(r"TC\d+[_]?", name, maxsplit=1)
    tail = after_tc[1].strip("_ ") if len(after_tc) > 1 else name

    # Remove noise
    tail = re.sub(r"^(FS|PP|CM|BR|RMCM)[_ ]+", "", tail)
    tail = re.sub(r"^2[_ ]*(?:Updated)?[_ ]*", "", tail)
    tail = re.sub(r"(?:TEST SAMPLE|Test Sample)", "", tail)
    tail = re.sub(r"(?:Updated)", "", tail)
    tail = re.sub(r"Copy of 2", "", tail)
    # Remove date patterns
    tail = re.sub(r"[_ ]*\d{0,2}[A-Z]{0,3}\d{4}[_ ]*", " ", tail)
    tail = re.sub(r"[-_ ]*(1[A-C])\b", "", tail)
    # Clean underscores to spaces
    tail = tail.replace("_", " ")
    tail = re.sub(r"\s+", " ", tail).strip(" -_")

    if not tail:
        tail = guess_doc_type(filename)

    if len(tail) > 25:
        tail = tail[:25].rsplit(" ", 1)[0] + "..."

    return tc, tail + variant


def guess_doc_type(filename):
    parts = filename.replace(".pdf", "").split("_")
    for part in parts:
        if part in DOC_TYPE_MAP:
            return DOC_TYPE_MAP[part]
    return "Marketing Material"


def build_metadata(filename):
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


# def submit_from_mongo(doc):
#     filename = doc["filename"]
#     metadata = build_metadata(filename)
#     file_bytes = bytes(doc["file_data"])
#     files = {"file": (filename, file_bytes, "application/pdf")}
#     data = {"metadata": json.dumps(metadata)}
#     response = requests.post(API_URL, files=files, data=data, timeout=600)
#     return response, metadata
#
#
# def submit_document(filepath):
#     filename = filepath.name
#     metadata = build_metadata(filename)
#     with open(filepath, "rb") as f:
#         files = {"file": (filename, f, "application/pdf")}
#         data = {"metadata": json.dumps(metadata)}
#         response = requests.post(API_URL, files=files, data=data, timeout=600)
#     return response, metadata


def _api_headers():
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": AUTH_TOKEN,
    }


def _post_analyze(filename, metadata):
    """POST to /analyze, then poll /output/{process_id} until done."""
    gcs_uri = f"{GCS_BUCKET}/{filename}"
    url = f"{API_BASE}/analyze?gcs_uri={quote(gcs_uri, safe='')}"
    headers = _api_headers()

    # 1. Kick off the analysis
    resp = requests.post(url, headers=headers, json=metadata, timeout=60)
    resp.raise_for_status()
    process_id = resp.json().get("process_id")
    if not process_id:
        return resp, metadata  # fallback: return as-is

    # 2. Poll /output/{process_id} until COMPLETED or timeout
    poll_url = f"{API_BASE}/output/{process_id}"
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        poll_resp = requests.get(poll_url, headers=headers, timeout=30)
        poll_resp.raise_for_status()
        data = poll_resp.json()
        status = data.get("status", "")
        if status not in ("PENDING", "STARTED", "PROCESSING"):
            return poll_resp, metadata

    # Timed out — return last poll response
    return poll_resp, metadata


def submit_from_mongo(doc):
    filename = doc["filename"]
    metadata = build_metadata(filename)
    return _post_analyze(filename, metadata)


def submit_document(filepath):
    filename = filepath.name
    metadata = build_metadata(filename)
    return _post_analyze(filename, metadata)


def parse_findings_from_response(resp_text):
    try:
        data = json.loads(resp_text)
    except Exception:
        return {}
    categories = Counter()
    total = 0
    for source_key in ["raw_output", "sequential_reasoner"]:
        source_data = data.get(source_key, {})
        if not isinstance(source_data, dict):
            continue
        for art_key in ARTIFACT_TYPES:
            art = source_data.get(art_key, {})
            if not isinstance(art, dict):
                continue
            for s in art.get("sections", []):
                cat = s.get("category", "Uncategorized")
                categories[cat] += 1
                total += 1
        if total > 0:
            break
    return dict(categories)


# ---------------------------------------------------------------------------
# Load test documents
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600)
def load_test_docs():
    db = get_mongo()
    return list(db["test_documents"].find().sort("filename", 1))


test_docs = load_test_docs()
use_mongo = len(test_docs) > 0

if not use_mongo:
    pdf_files = sorted(TEST_DOCS_DIR.glob("*.pdf"))
    if not pdf_files:
        st.error("No test documents found.")
        st.stop()

items = test_docs if use_mongo else pdf_files
items = sorted(items, key=lambda x: tc_sort_key(x["filename"] if use_mongo else x.name))
doc_count = len(items)

if "results" not in st.session_state:
    st.session_state["results"] = {}

results = st.session_state["results"]

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("PO2 Test Bench")

now_str = datetime.now().strftime("%b %d, %Y  %I:%M %p")
passed = sum(1 for r in results.values() if r.get("success"))
failed = sum(1 for r in results.values() if not r.get("success"))
pending = doc_count - len(results)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Test Cases", doc_count)
m2.metric("Passed", passed)
m3.metric("Failed", failed)
m4.metric("Pending", pending)

st.caption(f"NW Testing Team | {now_str} | ~5 min per test case")
st.divider()

if st.button("Clear Results"):
    st.session_state["results"] = {}
    st.rerun()

# ---------------------------------------------------------------------------
# Two-column layout: TC buttons | Dashboard
# ---------------------------------------------------------------------------
col_left, col_right = st.columns([2, 3])

# ---- LEFT: Test case buttons ----
with col_left:
    st.markdown("#### Test Cases")

    for item in items:
        name = item["filename"] if use_mongo else item.name
        tc, desc = short_name(name)
        result = results.get(name)

        if result:
            icon = "\u2705" if result["success"] else "\u274c"
        else:
            icon = "\u23f3"

        btn_label = f"{icon} {tc} | {desc}"

        if st.button(btn_label, key=f"run_{name}", use_container_width=True):
            with st.spinner(f"Running {tc}..."):
                try:
                    if use_mongo:
                        resp, meta = submit_from_mongo(item)
                    else:
                        resp, meta = submit_document(item)
                    st.session_state["results"][name] = {
                        "status_code": resp.status_code,
                        "success": resp.status_code == 200,
                        "response": resp.text[:5000],
                        "findings": parse_findings_from_response(resp.text) if resp.status_code == 200 else {},
                        "timestamp": datetime.now().isoformat(),
                    }
                except Exception as e:
                    st.session_state["results"][name] = {
                        "status_code": 0, "success": False,
                        "response": str(e), "findings": {},
                        "timestamp": datetime.now().isoformat(),
                    }
            st.rerun()

# ---- RIGHT: Dashboard ----
with col_right:
    st.markdown("#### Results Dashboard")

    if not results:
        st.info("Run a test case to see results here.")
    else:
        # Aggregate findings across all completed runs
        all_themes = Counter()
        total_findings = 0

        for r in results.values():
            if r.get("success") and r.get("findings"):
                for cat, count in r["findings"].items():
                    theme = cat_to_theme(cat)
                    all_themes[theme] += count
                    total_findings += count

        if total_findings > 0:
            st.metric("Total Findings", total_findings)

            st.markdown("**By Theme**")
            # Sort by theme order (T1, T2, ... T9)
            sorted_themes = sorted(all_themes.items(), key=lambda x: THEME_ORDER.get(x[0], 99))
            for theme, count in sorted_themes:
                pct = count / total_findings * 100
                st.progress(pct / 100, text=f"{theme}: **{count}** ({pct:.0f}%)")

        # Per-TC breakdown
        st.markdown("---")
        st.markdown("**Per Test Case**")
        for item in items:
            name = item["filename"] if use_mongo else item.name
            tc, desc = short_name(name)
            r = results.get(name)
            if not r:
                continue
            if r["success"] and r["findings"]:
                count = sum(r["findings"].values())
                # Show top themes
                tc_themes = Counter()
                for cat, n in r["findings"].items():
                    tc_themes[cat_to_theme(cat)] += n
                top = ", ".join(f"{t}({n})" for t, n in sorted(tc_themes.items(), key=lambda x: THEME_ORDER.get(x[0], 99))[:3])
                st.caption(f"\u2705 **{tc}**: {count} findings — {top}")
            elif r["success"]:
                st.caption(f"\u2705 **{tc}**: No findings parsed")
                with st.expander(f"Debug: {tc} raw response"):
                    st.code(r.get("response", ""), language="json")
            else:
                st.caption(f"\u274c **{tc}**: Failed ({r['status_code']})")
                with st.expander(f"Debug: {tc} raw response"):
                    st.code(r.get("response", ""), language="json")
