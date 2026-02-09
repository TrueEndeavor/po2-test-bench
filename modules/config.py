from pathlib import Path

API_URL = "http://34.63.177.131:8000/analyze"
TEST_DOCS_DIR = Path(__file__).resolve().parent.parent / "test_docs"

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

THEME_ORDER = {v: i for i, v in enumerate(CATEGORY_THEMES.values())}

REVIEW_FIELDS = ["accept", "accept_with_changes", "reject", "reject_reason"]
