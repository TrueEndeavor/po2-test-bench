import json
from collections import Counter
from modules.config import ARTIFACT_TYPES, CATEGORY_THEMES, THEME_ORDER


def cat_to_theme(cat):
    """Map a raw category name to its short theme label (T1-T9)."""
    for key, theme in CATEGORY_THEMES.items():
        if key.lower() in cat.lower() or cat.lower() in key.lower():
            return theme
    for key, theme in CATEGORY_THEMES.items():
        if key.split()[0].lower() in cat.lower():
            return theme
    return cat[:25]


def parse_findings_summary(resp_text):
    """Parse API response text -> {category: count} for summary display."""
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


def extract_findings_for_review(doc_id, data, source_key="raw_output"):
    """Extract per-finding rows with DB path metadata for write-back.

    Args:
        doc_id: MongoDB document _id (string)
        data: The source dict (e.g. doc["raw_output"] or parsed API response)
        source_key: "raw_output" or "sequential_reasoner"

    Returns:
        List of dicts with finding fields + _doc_id/_source/_art_key/_section_idx
    """
    rows = []
    for art_key in ARTIFACT_TYPES:
        art = data.get(art_key, {})
        if not isinstance(art, dict):
            continue
        for idx, s in enumerate(art.get("sections", [])):
            rows.append({
                "_doc_id": str(doc_id),
                "_source": source_key,
                "_art_key": art_key,
                "_section_idx": idx,
                "artifact_type": art_key.replace("_artifact", ""),
                "sentence": s.get("sentence", ""),
                "page": s.get("page_number", ""),
                "rule_citation": s.get("rule_citation", ""),
                "recommendations": s.get("recommendations", ""),
                "category": s.get("category", "N/A"),
                "observations": s.get("observations", ""),
                "summary": s.get("summary", ""),
                "accept": bool(s.get("accept", False)),
                "accept_with_changes": bool(s.get("accept_with_changes", False)),
                "reject": bool(s.get("reject", False)),
                "reject_reason": s.get("reject_reason", "") or "",
            })
    return rows
