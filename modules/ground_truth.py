"""
Ground truth loading and comparison utilities for PO2 Test Bench.
"""
from pathlib import Path
import pandas as pd


GROUND_TRUTH_CSV = Path(__file__).resolve().parent.parent / "ground_truth.csv"


def load_ground_truth():
    """Load validated ground truth CSV and return set of (tc, page, sentence_prefix) keys."""
    if not GROUND_TRUTH_CSV.exists():
        return set(), pd.DataFrame()

    gt_df = pd.read_csv(GROUND_TRUTH_CSV)
    gt_df.columns = gt_df.columns.str.strip()

    # Include ALL categories (no filtering)
    # Categories: Misleading, Inadequate or Missing Disclosures, Rankings/Ratings

    # Build lookup keys: (tc_number, page, first 50 chars of non-compliant sentence)
    keys = set()
    for _, row in gt_df.iterrows():
        tc = str(row.get("TC Id", "")).strip()
        page = int(row.get("page_number", 0)) if pd.notna(row.get("page_number")) else 0
        sentence = str(row.get("sentence", "")).strip()
        if tc and sentence:
            keys.add((tc, page, sentence[:50].lower()))

    return keys, gt_df


def is_ground_truth(tc_number, page, sentence, gt_keys):
    """Check if a finding matches any ground truth entry."""
    if not gt_keys:
        return False

    tc = str(tc_number).strip()

    # Handle empty strings and NaN values
    try:
        pg = int(page) if (pd.notna(page) and str(page).strip()) else 0
    except (ValueError, TypeError):
        pg = 0

    sent = str(sentence).strip().lower()

    # Exact prefix match (first 50 chars)
    if (tc, pg, sent[:50]) in gt_keys:
        return True

    # Fallback: check if any GT sentence prefix is contained in the finding
    for gt_tc, gt_pg, gt_prefix in gt_keys:
        if tc == gt_tc and pg == gt_pg and gt_prefix and gt_prefix in sent:
            return True

    return False


def calculate_gt_metrics(findings_list, gt_keys, gt_df, tc_number):
    """
    Calculate ground truth comparison metrics using weighted scoring.

    Scoring:
    - 1.0: Exact sentence match (TP)
    - 0.5: Same theme + page but different sentence (Partial TP)
    - 0.0: Different theme or not in GT (FP)

    Only findings from GT themes are counted; other themes are suppressed.

    Args:
        findings_list: List of finding dicts with 'page', 'sentence', 'category' keys
        gt_keys: Set of GT keys from load_ground_truth()
        gt_df: Ground truth DataFrame
        tc_number: Test case number (e.g., "TC01")

    Returns:
        dict with tp, partial_tp, fp, fn, precision, recall, f1, and detailed_findings list
    """
    # Filter GT entries for this TC
    tc_gt = gt_df[gt_df["TC Id"].str.strip().str.upper() == tc_number.upper()]
    expected_count = len(tc_gt)

    # Get valid themes for this TC from GT
    valid_themes = set()
    for _, row in tc_gt.iterrows():
        category = str(row.get("category", "")).strip()
        if category:
            valid_themes.add(category.lower())

    # Build theme-based GT lookup: (tc, page, category) -> list of sentences
    gt_theme_map = {}
    for _, row in tc_gt.iterrows():
        page = int(row.get("page_number", 0)) if pd.notna(row.get("page_number")) else 0
        category = str(row.get("category", "")).strip().lower()
        sentence = str(row.get("sentence", "")).strip()
        key = (tc_number.upper(), page, category)
        if key not in gt_theme_map:
            gt_theme_map[key] = []
        gt_theme_map[key].append(sentence)

    # Simple scoring: TP, FP, or Suppressed
    tp = 0
    fp = 0
    suppressed = 0
    detailed_findings = []

    for finding in findings_list:
        page = finding.get("page", 0)
        try:
            page = int(page) if pd.notna(page) else 0
        except (ValueError, TypeError):
            page = 0

        sentence = str(finding.get("sentence", "")).strip()
        category = str(finding.get("category", "")).strip().lower()

        # Check if this finding's category is in GT for this TC
        if category not in valid_themes:
            # Suppress findings from categories not in GT
            suppressed += 1
            detailed_findings.append({
                **finding,
                "gt_status": "Suppressed",
                "match_type": "Category not in GT"
            })
            continue

        # Check for exact match (only for findings in GT categories)
        is_exact = is_ground_truth(tc_number, page, sentence, gt_keys)

        if is_exact:
            tp += 1
            detailed_findings.append({
                **finding,
                "gt_status": "TP",
                "match_type": "Exact match"
            })
        else:
            fp += 1
            detailed_findings.append({
                **finding,
                "gt_status": "FP",
                "match_type": "Not in GT"
            })

    # False negatives = GT entries not found by API
    fn = expected_count - tp

    # Simple metrics (only count TP + FP, exclude suppressed)
    total_found = len(findings_list)
    total_relevant = tp + fp  # Only findings in GT categories
    precision = tp / total_relevant if total_relevant > 0 else 0.0
    recall = tp / expected_count if expected_count > 0 else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "suppressed": suppressed,
        "expected": expected_count,
        "found": total_found,
        "relevant_found": total_relevant,  # TP + FP (excluding suppressed)
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "detailed_findings": detailed_findings,
    }


def get_missing_gt_findings(gt_df, tc_number, found_findings, gt_keys):
    """
    Get list of GT findings that were NOT found by the API (false negatives).

    Args:
        gt_df: Ground truth DataFrame
        tc_number: Test case number
        found_findings: List of findings found by API
        gt_keys: Set of GT keys

    Returns:
        List of missing GT findings with details
    """
    tc_gt = gt_df[gt_df["TC Id"].str.strip().str.upper() == tc_number.upper()]
    missing = []

    for _, gt_row in tc_gt.iterrows():
        page = int(gt_row.get("Page Number", 0)) if pd.notna(gt_row.get("Page Number")) else 0
        sentence = str(gt_row.get("Non compliant", "")).strip()

        # Check if this GT finding was found by API
        found = False
        for finding in found_findings:
            if is_ground_truth(tc_number, finding.get("page"), finding.get("sentence"), gt_keys):
                # Check if it matches this specific GT entry
                if page == finding.get("page") and sentence[:50].lower() in str(finding.get("sentence", "")).lower():
                    found = True
                    break

        if not found:
            missing.append({
                "page": page,
                "sentence": sentence,
                "category": gt_row.get("category", ""),
                "sub_bucket": gt_row.get("sub_bucket", ""),
                "reasoning": gt_row.get("observations", ""),
                "rule_citation": gt_row.get("rule_citation", ""),
            })

    return missing
