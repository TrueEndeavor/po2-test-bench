import os
import re
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from bson import ObjectId
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Silver-to-Gold Curation",
    page_icon="🥇",
    layout="wide",
)

# Compact layout CSS
st.markdown(
    """<style>
    .block-container { padding-top: 5.5rem; padding-bottom: 0; }
    [data-testid="stExpander"] { margin-bottom: 0.3rem; }
    [data-testid="stExpander"] details summary { padding: 0.4rem 0.6rem; }
    [data-testid="stVerticalBlock"] > div { gap: 0.3rem; }
    div[data-testid="stDataFrame"] { margin-bottom: 0; }
    .gt-row { background: #c8f7c5 !important; border-left: 4px solid #6bcb77 !important;
              padding: 4px 10px; border-radius: 4px; margin: 2px 0; }
    .gt-badge { background: #c8f7c5; color: #2d6a2e; padding: 2px 8px;
                border-radius: 4px; font-weight: 600; font-size: 0.85em; }
    .gt-table [data-testid="stDataFrame"] { background: #e8fbe8; border-left: 3px solid #6bcb77;
              border-radius: 4px; }
    </style>""",
    unsafe_allow_html=True,
)


def css_safe(name):
    return re.sub(r'[^a-zA-Z0-9]', '-', str(name)).strip('-')

from modules.db import (
    get_golden_outputs_collection,
    soft_delete_batch,
    load_deletion_keys,
    get_deletion_count,
    undo_soft_delete,
    lock_category,
    unlock_category,
    get_category_statuses,
)
from modules.parsers import extract_findings_for_review, cat_to_theme
from modules.naming import tc_sort_key, short_name
from modules.config import ARTIFACT_TYPES, CATEGORY_THEMES, THEME_ORDER, TEST_DOCS_DIR

# ---------------------------------------------------------------------------
# Ground truth loading & matching
# ---------------------------------------------------------------------------
GROUND_TRUTH_CSV = Path(__file__).resolve().parent / "ground_truth.csv"


@st.cache_data(ttl=300)
def load_ground_truth():
    """Load validated ground truth CSV and return set of (tc, page, sentence_prefix) keys."""
    if not GROUND_TRUTH_CSV.exists():
        return set(), pd.DataFrame()
    gt_df = pd.read_csv(GROUND_TRUTH_CSV)
    gt_df.columns = gt_df.columns.str.strip()
    # Build lookup keys: (tc_number, page, first 50 chars of non-compliant sentence)
    keys = set()
    for _, row in gt_df.iterrows():
        tc = str(row.get("TC Id", "")).strip()
        page = int(row.get("Page Number", 0)) if pd.notna(row.get("Page Number")) else 0
        sentence = str(row.get("Non compliant", "")).strip()
        if tc and sentence:
            keys.add((tc, page, sentence[:50].lower()))
    return keys, gt_df


def is_ground_truth(tc_number, page, sentence, gt_keys):
    """Check if a finding matches any ground truth entry."""
    if not gt_keys:
        return False
    tc = str(tc_number).strip()
    pg = int(page) if pd.notna(page) else 0
    sent = str(sentence).strip().lower()
    # Exact prefix match (first 50 chars)
    if (tc, pg, sent[:50]) in gt_keys:
        return True
    # Fallback: check if any GT sentence prefix is contained in the finding
    for gt_tc, gt_pg, gt_prefix in gt_keys:
        if tc == gt_tc and pg == gt_pg and gt_prefix and gt_prefix in sent:
            return True
    return False

# ---------------------------------------------------------------------------
# Active category — only T1 for now; unlock more as curation progresses
# ---------------------------------------------------------------------------
ACTIVE_CATEGORIES = [
    "Misleading or Unsubstantiated Claims",   # T1
    # "Performance Presentation & Reporting",   # T2 — unlock later
    # "Inadequate or Missing Disclosures",      # T3
    # "Testimonials & Endorsements",            # T4
    # "Digital & Distribution Controls",        # T5
    # "Comparisons and Competitive Claims",     # T6
    # "Ratings & Data Context Validation",      # T7
    # "Improper Use of Third-Party Content & Intellectual Property",  # T8
    # "Editorial (Non-Regulatory)",             # T9
]
ACTIVE_THEMES = [CATEGORY_THEMES[c] for c in ACTIVE_CATEGORIES]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
@st.cache_data(ttl=120)
def load_golden_records():
    coll = get_golden_outputs_collection()
    return list(coll.find().sort("created_at", -1))


@st.cache_data(ttl=60)
def cached_deletion_keys():
    return load_deletion_keys()


@st.cache_data(ttl=60)
def cached_category_statuses():
    return get_category_statuses()


def extract_golden_findings(doc, source="raw_output"):
    """Adapt golden_outputs structure for extract_findings_for_review()."""
    doc_id = str(doc["_id"])
    api_response = doc.get("api_response", {})
    data = api_response.get(source, {})
    actual_source = source

    if not data or not isinstance(data, dict):
        fallback = "raw_output" if source == "sequential_reasoner" else "sequential_reasoner"
        data = api_response.get(fallback, {})
        actual_source = fallback

    if not data or not isinstance(data, dict):
        return []

    rows = extract_findings_for_review(doc_id, data, actual_source)
    tc, desc = short_name(doc.get("filename", ""))
    for r in rows:
        r["tc_number"] = doc.get("tc_number", tc)
        r["filename"] = doc.get("filename", "")
        r["run_label"] = doc.get("run_label", "")
        art = data.get(r["_art_key"], {})
        sections = art.get("sections", [])
        if r["_section_idx"] < len(sections):
            r["sub_bucket"] = sections[r["_section_idx"]].get("sub_bucket", "")
        else:
            r["sub_bucket"] = ""
    return rows


def find_pdf_path(filename):
    """Find the PDF file in test_docs matching the filename."""
    if not filename:
        return None
    pdf_path = TEST_DOCS_DIR / filename
    if pdf_path.exists():
        return str(pdf_path)
    for f in TEST_DOCS_DIR.glob("*.pdf"):
        if filename.replace(".pdf", "") in f.name:
            return str(f)
    return None


# ---------------------------------------------------------------------------
# Compact header: title | TC filter | refresh — one row
# ---------------------------------------------------------------------------
golden_records = load_golden_records()

# Run label filter - only show golden_v* baselines (oldest first)
run_labels = sorted(
    set(r.get("run_label", "") for r in golden_records)
)
golden_labels = [rl for rl in run_labels if rl.startswith("golden_v")]

st.title("PO2 Golden Dataset Admin")

col_title, col_baseline = st.columns([3, 1])

with col_title:
    st.markdown("### 🥇 Silver-to-Gold Curation")

with col_baseline:
    st.write("")  # Spacer for alignment
    if golden_labels:
        selected_run_label = st.selectbox(
            "Baseline",
            golden_labels,
            help="Select the golden baseline to curate",
            label_visibility="collapsed",
        )
    else:
        st.error("No baseline found")
        st.stop()

if not golden_labels:
    st.error("No golden baseline found. Use the **Capture Baseline** page to create one.")
    st.stop()

# Filter records by selected run label
golden_records = [r for r in golden_records if r.get("run_label") == selected_run_label]

tc_options = sorted(
    set(r.get("tc_number", "") for r in golden_records),
    key=tc_sort_key,
)

selected_tc = st.pills(
    "Test Cases", tc_options, selection_mode="single",
)
selected_tcs = [selected_tc] if selected_tc else []

if st.button("Refresh", key="refresh_btn"):
    st.cache_data.clear()
    for key in list(st.session_state.keys()):
        if key.startswith("golden_editor"):
            del st.session_state[key]
    st.rerun()

st.caption(
    "Review API findings, delete incorrect ones, keep what becomes your **golden dataset**. "
    f"Active: **{', '.join(ACTIVE_THEMES)}** | "
    "💡 Use the **Capture Baseline** page to create new baselines."
)

if not selected_tcs:
    st.info("Select one or more test cases above to begin curation.")
    st.stop()

# ---------------------------------------------------------------------------
# Filter, flatten, and apply soft-delete mask
# ---------------------------------------------------------------------------
filtered = [
    r for r in golden_records
    if r.get("tc_number", "") in selected_tcs
]

if not filtered:
    st.warning("No golden records match the current filters.")
    st.stop()

all_rows = []
for doc in filtered:
    all_rows.extend(extract_golden_findings(doc, source="raw_output"))

if not all_rows:
    st.info("No findings found in the selected records.")
    st.stop()

df = pd.DataFrame(all_rows)
df["theme"] = df["category"].apply(cat_to_theme)

# --- Ground truth matching ---
gt_keys, gt_df = load_ground_truth()
df["is_gt"] = df.apply(
    lambda r: is_ground_truth(r["tc_number"], r["page"], r["sentence"], gt_keys),
    axis=1,
)

# --- Soft-delete filtering: hide findings that were previously deleted ---
deletion_keys = cached_deletion_keys()

if deletion_keys:
    mask = [
        (r["_doc_id"], r["_source"], r["_art_key"], r["_section_idx"]) not in deletion_keys
        for _, r in df.iterrows()
    ]
    df = df[mask].reset_index(drop=True)

# --- Filter out sec_typographycheck results ---
df = df[
    ~df["_source"].str.contains("sec_typographycheck", case=False, na=False) &
    ~df["run_label"].astype(str).str.contains("sec_typographycheck", case=False, na=False) &
    ~df["artifact_type"].astype(str).str.contains("sec_typographycheck", case=False, na=False)
].reset_index(drop=True)

total = len(df)

# ---------------------------------------------------------------------------
# Selected TCs banner with PDF links
# ---------------------------------------------------------------------------
tc_doc_map = {}
tc_pdf_map = {}
for doc in filtered:
    tc, desc = short_name(doc.get("filename", ""))
    tc_key = doc.get("tc_number", tc)
    tc_doc_map[tc_key] = desc
    pdf_path = find_pdf_path(doc.get("filename", ""))
    if pdf_path:
        tc_pdf_map[tc_key] = pdf_path

banner_parts = []
for tc in sorted(tc_doc_map, key=tc_sort_key):
    desc = tc_doc_map[tc]
    banner_parts.append(f"**{tc}** — {desc}")

active_count = len(df[df["category"].isin(ACTIVE_CATEGORIES)])
gt_count_total = df["is_gt"].sum()
gt_label = f"  ·  <span class='gt-badge'>{gt_count_total} GT</span>" if gt_count_total else ""
st.markdown(
    f"#### {'  ·  '.join(banner_parts)}  ·  {active_count} findings{gt_label}",
    unsafe_allow_html=True,
)

# PDF download links
pdf_cols = st.columns(len(tc_pdf_map)) if tc_pdf_map else []
for i, (tc, path) in enumerate(sorted(tc_pdf_map.items(), key=lambda x: tc_sort_key(x[0]))):
    with pdf_cols[i]:
        with open(path, "rb") as f:
            st.download_button(
                f"Open {tc} PDF",
                data=f.read(),
                file_name=os.path.basename(path),
                mime="application/pdf",
                key=f"pdf_{tc}",
            )

# ---------------------------------------------------------------------------
# Category lock status & CSV Download
# ---------------------------------------------------------------------------
cat_statuses = cached_category_statuses()

# CSV download button - only includes locked categories' golden findings
locked_cats = [cat for cat, status in cat_statuses.items() if status.get("status") == "locked"]

if locked_cats:
    # Filter to only locked categories
    locked_df = df[df["category"].isin(locked_cats)].copy()

    if not locked_df.empty:
        # Select columns for CSV export
        export_cols = [
            "tc_number", "filename", "page", "category", "sub_bucket",
            "artifact_type", "sentence", "rule_citation", "observations",
            "recommendations", "summary"
        ]
        export_df = locked_df[[col for col in export_cols if col in locked_df.columns]]

        csv_data = export_df.to_csv(index=False)

        st.download_button(
            f"📥 Download Golden CSV ({len(locked_cats)} locked categories, {len(export_df)} findings)",
            data=csv_data,
            file_name=f"golden_dataset_{selected_run_label}.csv",
            mime="text/csv",
            key="download_golden_csv",
            use_container_width=False,
        )
        st.caption(f"✓ CSV includes only **locked** categories with all soft-deletes and sec_typographycheck filtered out.")
    else:
        st.info("No locked findings available for download yet.")
else:
    st.info("Lock at least one category to enable CSV download of golden findings.")

st.divider()

# ---------------------------------------------------------------------------
# Tabs: Curate | Browse Details | Add Ground Truth
# ---------------------------------------------------------------------------
tab_curate, tab_details, tab_add = st.tabs([
    "Curate", "Browse Details", "Add Ground Truth"
])

# ========================== TAB: CURATE ====================================

# Build a global sub-bucket -> color map so chart and table colors match
_all_sbs = sorted(df["sub_bucket"].unique()) if len(df) > 0 else []
_palette = px.colors.qualitative.Pastel + px.colors.qualitative.Set3
SB_COLOR_MAP = {sb: _palette[i % len(_palette)] for i, sb in enumerate(_all_sbs)}

with tab_curate:

    # Only show active categories (T1 for now)
    display_df = df[df["category"].isin(ACTIVE_CATEGORIES)].copy()

    if display_df.empty:
        st.info("No findings in the active categories. All curated!")
        st.stop()

    display_df["theme"] = display_df["category"].apply(cat_to_theme)

    display_df = display_df.sort_values(
        ["theme", "sub_bucket", "tc_number"]
    ).reset_index(drop=True)

    # GT rows get no delete checkbox; non-GT get one
    display_df.insert(0, "delete", False)
    # Force GT rows to never have delete checked
    display_df.loc[display_df["is_gt"] == True, "delete"] = False

    visible_cols = ["delete", "page", "artifact_type", "sentence"]

    col_config = {
        "delete": st.column_config.CheckboxColumn("Del", width="small"),
        "page": st.column_config.NumberColumn("Pg", width="small", disabled=True),
        "artifact_type": st.column_config.TextColumn("Source", width="small", disabled=True),
        "sentence": st.column_config.TextColumn("sentence", width="large", disabled=True),
    }

    if "last_action" in st.session_state:
        st.success(st.session_state.pop("last_action"))

    theme_list = sorted(display_df["theme"].unique(), key=lambda t: THEME_ORDER.get(t, 99))

    for theme in theme_list:
        theme_df = display_df[display_df["theme"] == theme]

        # Extra safety: ensure soft-deleted items are filtered out from sub-tables
        if deletion_keys:
            theme_mask = [
                (r["_doc_id"], r["_source"], r["_art_key"], r["_section_idx"]) not in deletion_keys
                for _, r in theme_df.iterrows()
            ]
            theme_df = theme_df[theme_mask].reset_index(drop=True)

        count_in_theme = len(theme_df)

        # Get the category name for this theme (for lock status)
        theme_category = [c for c, t in CATEGORY_THEMES.items() if t == theme]
        cat_name = theme_category[0] if theme_category else theme
        cat_status = cat_statuses.get(cat_name, {})
        is_locked = cat_status.get("status") == "locked"

        sb_summary = theme_df.groupby("sub_bucket").size().sort_values(ascending=False)

        lock_icon = "🔒 " if is_locked else ""
        with st.expander(f"{lock_icon}**{theme}**", expanded=False):

            if is_locked:
                st.info(
                    f"**{theme} is locked.** "
                    f"Curated on {cat_status.get('locked_at', '?')}. "
                    f"Before: {cat_status.get('findings_before', '?')}, "
                    f"After: {cat_status.get('findings_after', '?')}, "
                    f"Deleted: {cat_status.get('deletions', '?')}."
                )

            col_chart, col_table = st.columns([1, 3])

            with col_chart:
                sb_chart = (
                    sb_summary.reset_index()
                    .rename(columns={"sub_bucket": "Sub-Bucket", 0: "Count"})
                )
                fig = px.bar(
                    sb_chart,
                    y="Sub-Bucket",
                    x="Count",
                    orientation="h",
                    height=max(100, len(sb_chart) * 36 + 40),
                    text="Count",
                    color="Sub-Bucket",
                    color_discrete_map=SB_COLOR_MAP,
                )
                fig.update_layout(
                    margin=dict(l=0, r=0, t=0, b=0),
                    showlegend=False,
                    yaxis=dict(title="", tickfont=dict(size=11)),
                    xaxis=dict(title="", showticklabels=False),
                )
                fig.update_traces(textposition="outside", textfont_size=11)
                st.plotly_chart(fig, use_container_width=True, key=f"chart_{theme}")

            with col_table:
                all_theme_edited = []
                for sb_name in sb_summary.index:
                    sb_df = theme_df[theme_df["sub_bucket"] == sb_name]
                    sb_color = SB_COLOR_MAP.get(sb_name, "#ccc")

                    safe_id = f"sb-{css_safe(theme)}-{css_safe(sb_name)}"

                    gt_count = int(sb_df["is_gt"].sum())
                    label_parts = f"<b>{sb_name}</b> &nbsp;({len(sb_df)}"
                    if gt_count:
                        label_parts += f" &middot; <span class='gt-badge'>{gt_count} GT</span>"
                    label_parts += ")"

                    # Colored header
                    st.markdown(
                        f"<div class='{safe_id}' style='border-left:4px solid {sb_color}; "
                        f"background:{sb_color}22; padding:4px 10px; "
                        f"margin:4px 0 0 0; border-radius:3px 3px 0 0;'>"
                        f"{label_parts}</div>"
                        f"<style>"
                        f".{safe_id} + div [data-testid='stDataFrame'] {{"
                        f"  background: {sb_color}0d; border-left: 3px solid {sb_color};"
                        f"  border-radius: 0 0 4px 4px;"
                        f"}}"
                        f"</style>",
                        unsafe_allow_html=True,
                    )

                    editor_key = f"golden_editor_{theme}_{sb_name}"

                    # GT rows: inline green HTML rows (no delete)
                    gt_rows_df = sb_df[sb_df["is_gt"] == True]
                    non_gt_df = sb_df[sb_df["is_gt"] == False]

                    for _, gt_r in gt_rows_df.iterrows():
                        sent_preview = str(gt_r["sentence"])[:120]
                        st.markdown(
                            f"<div style='background:#c8f7c5; padding:5px 10px; "
                            f"margin:1px 0; border-radius:3px; display:flex; "
                            f"align-items:center; gap:10px; font-size:0.9em;'>"
                            f"<span style='color:#2d6a2e; font-weight:700; "
                            f"min-width:28px;'>Pg {gt_r['page']}</span>"
                            f"<span>{sent_preview}</span>"
                            f"<span class='gt-badge' style='margin-left:auto; "
                            f"white-space:nowrap;'>GT</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                    # Non-GT rows: data_editor with delete checkbox
                    if is_locked:
                        if not non_gt_df.empty:
                            st.dataframe(
                                non_gt_df[["page", "sentence"]],
                                use_container_width=True,
                                hide_index=True,
                            )
                        all_theme_edited.append((non_gt_df, None))
                    else:
                        if not non_gt_df.empty:
                            edited = st.data_editor(
                                non_gt_df[visible_cols],
                                use_container_width=True,
                                hide_index=True,
                                num_rows="fixed",
                                key=editor_key,
                                column_config=col_config,
                            )
                            all_theme_edited.append((non_gt_df, edited))
                        else:
                            all_theme_edited.append((non_gt_df, None))

                    # Detail popovers
                    sb_rows = []
                    for _, r in sb_df.iterrows():
                        sb_rows.append({
                            "page": r["page"],
                            "rule_citation": r["rule_citation"],
                            "observations": r["observations"],
                            "recommendations": r["recommendations"],
                            "summary": r["summary"],
                            "category": r["category"],
                            "sub_bucket": r.get("sub_bucket", ""),
                            "sentence": r.get("sentence", ""),
                            "_doc_id": r["_doc_id"],
                            "_source": r["_source"],
                            "_art_key": r["_art_key"],
                            "_section_idx": r["_section_idx"],
                            "is_gt": r.get("is_gt", False),
                        })

                    detail_cols = st.columns(min(len(sb_rows), 6))
                    for j, rd in enumerate(sb_rows):
                        pop_key = f"pop_{theme}_{sb_name}_{j}"
                        confirm_key = f"pop_confirm_{pop_key}"
                        is_gt_finding = rd.get("is_gt", False)
                        pop_label = f"Pg {rd['page']}"
                        with detail_cols[j % len(detail_cols)]:
                            with st.popover(pop_label, use_container_width=True):
                                if is_gt_finding:
                                    st.markdown(
                                        "<span class='gt-badge'>Ground Truth (Validated)</span>",
                                        unsafe_allow_html=True,
                                    )
                                st.markdown(f"> {rd['sentence']}")
                                st.markdown(f"**rule_citation:** {rd['rule_citation']}")
                                st.markdown(f"**observations:** {rd['observations']}")
                                st.markdown(f"**recommendations:** {rd['recommendations']}")
                                st.markdown(f"**summary:** {rd['summary']}")
                                st.divider()
                                if is_gt_finding:
                                    st.caption("Ground truth — frozen, cannot be deleted.")
                                elif is_locked:
                                    st.caption("Category is locked — no deletions.")
                                elif not st.session_state.get(confirm_key, False):
                                    if st.button(
                                        "Delete",
                                        key=f"pop_del_{pop_key}",
                                        type="primary",
                                        use_container_width=True,
                                    ):
                                        st.session_state[confirm_key] = True
                                        st.rerun()
                                else:
                                    st.warning("Delete this finding?")
                                    if st.button("Yes, delete", key=f"pop_yes_{pop_key}", type="primary", use_container_width=True):
                                        soft_delete_batch([{
                                            "_doc_id": rd["_doc_id"],
                                            "_source": rd["_source"],
                                            "_art_key": rd["_art_key"],
                                            "_section_idx": rd["_section_idx"],
                                            "category": rd.get("category", ""),
                                            "sub_bucket": rd.get("sub_bucket", ""),
                                            "sentence": rd.get("sentence", ""),
                                        }])
                                        st.cache_data.clear()
                                        st.session_state[confirm_key] = False
                                        st.session_state["last_action"] = "Soft-deleted 1 finding."
                                        st.rerun()
                                    if st.button("Cancel", key=f"pop_no_{pop_key}", use_container_width=True):
                                        st.session_state[confirm_key] = False
                                        st.rerun()

            # Bulk delete via checkboxes (only if not locked, skip GT rows)
            if not is_locked:
                theme_marked_indices = []
                for sb_df, edited in all_theme_edited:
                    if edited is not None:
                        marked = edited[edited["delete"] == True]
                        # Protect GT rows — never allow deletion
                        non_gt_marked = [
                            idx for idx in marked.index.tolist()
                            if not display_df.loc[idx, "is_gt"]
                        ]
                        theme_marked_indices.extend(non_gt_marked)

                marked_count = len(theme_marked_indices)
                pending_key = f"pending_delete_{theme}"

                col_btn, col_confirm = st.columns([1, 3])
                with col_btn:
                    if st.button(
                        f"Delete ({marked_count})",
                        key=f"del_btn_{theme}",
                        type="primary",
                        use_container_width=True,
                        disabled=(marked_count == 0),
                    ):
                        st.session_state[pending_key] = True

                if st.session_state.get(pending_key, False):
                    with col_confirm:
                        st.warning(f"Soft-delete **{marked_count}** finding(s) from {theme}?")
                    c_yes, c_no, _ = st.columns([1, 1, 4])
                    with c_yes:
                        if st.button("Confirm", key=f"confirm_{theme}", type="primary"):
                            deletions = []
                            for idx in theme_marked_indices:
                                row = display_df.loc[idx]
                                deletions.append({
                                    "_doc_id": row["_doc_id"],
                                    "_source": row["_source"],
                                    "_art_key": row["_art_key"],
                                    "_section_idx": row["_section_idx"],
                                    "category": row.get("category", ""),
                                    "sub_bucket": row.get("sub_bucket", ""),
                                    "sentence": row.get("sentence", ""),
                                })

                            count = soft_delete_batch(deletions)

                            st.session_state[pending_key] = False
                            st.session_state["last_action"] = f"Soft-deleted {count} finding(s) from {theme}."
                            st.cache_data.clear()
                            st.rerun()

                    with c_no:
                        if st.button("Cancel", key=f"cancel_{theme}"):
                            st.session_state[pending_key] = False
                            st.rerun()

            # --- Lock / Unlock category button ---
            st.divider()
            lock_col, status_col = st.columns([1, 3])
            with lock_col:
                if is_locked:
                    if st.button(f"Unlock {theme}", key=f"unlock_{theme}", use_container_width=True):
                        unlock_category(cat_name)
                        st.cache_data.clear()
                        st.session_state["last_action"] = f"Unlocked {theme} for re-curation."
                        st.rerun()
                else:
                    # Count total findings in this category BEFORE deletions
                    all_cat_df = pd.DataFrame(all_rows)
                    all_cat_df["theme"] = all_cat_df["category"].apply(cat_to_theme)
                    before_count = len(all_cat_df[all_cat_df["theme"] == theme])
                    after_count = count_in_theme

                    if st.button(
                        f"Lock {theme} ({after_count} findings)",
                        key=f"lock_{theme}",
                        type="primary",
                        use_container_width=True,
                    ):
                        lock_category(cat_name, before_count, after_count)
                        st.cache_data.clear()
                        st.session_state["last_action"] = (
                            f"Locked {theme}: {before_count} → {after_count} findings "
                            f"({before_count - after_count} deleted)."
                        )
                        st.rerun()
            with status_col:
                if not is_locked:
                    st.caption(
                        f"Lock when done curating. "
                        f"Original: {before_count}, Current: {after_count}, "
                        f"Deleted: {before_count - after_count}"
                    )


# ========================== TAB: BROWSE DETAILS ============================
with tab_details:

    if "detail_action" in st.session_state:
        st.success(st.session_state.pop("detail_action"))

    # Only show active categories here too
    browse_df = df[df["category"].isin(ACTIVE_CATEGORIES)]

    for i, row in browse_df.iterrows():
        is_gt_row = row.get("is_gt", False)

        if is_gt_row:
            status = "Ground Truth"
            color = "green"
        else:
            status = (
                "Accepted" if row["accept"]
                else "Rejected" if row["reject"]
                else "Accept w/ Changes" if row["accept_with_changes"]
                else "Unreviewed"
            )
            color = {
                "Accepted": "green", "Rejected": "red",
                "Accept w/ Changes": "orange", "Unreviewed": "gray",
            }[status]
        tc_label, tc_desc = short_name(row.get("filename", ""))
        preview = str(row["sentence"])[:80]
        label = f":{color}[**{status}**] {tc_label} | {row['artifact_type']} — {preview}"

        with st.expander(label, expanded=False):
            if is_gt_row:
                st.markdown(
                    "<span class='gt-badge'>Ground Truth (Validated)</span>",
                    unsafe_allow_html=True,
                )
            st.markdown(f"> {row['sentence']}")
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**{tc_label}** — {tc_desc}")
                st.markdown(f"**page:** {row['page']}")
                st.markdown(f"**category:** {row['category']}")
                st.markdown(f"**sub_bucket:** {row.get('sub_bucket', '')}")
                st.markdown(f"**rule_citation:** {row['rule_citation']}")
            with col_b:
                st.markdown(f"**observations:** {row['observations']}")
                st.markdown(f"**recommendations:** {row['recommendations']}")
                if row.get("reject_reason"):
                    st.markdown(f"**reject_reason:** :red[{row['reject_reason']}]")

            if is_gt_row:
                st.caption("Ground truth — frozen, cannot be deleted.")
            else:
                del_key = f"detail_del_{i}"
                confirm_key = f"detail_confirm_{i}"

                cat_name_browse = row["category"]
                is_cat_locked = cat_statuses.get(cat_name_browse, {}).get("status") == "locked"

                if is_cat_locked:
                    st.caption("Category is locked — no deletions.")
                else:
                    if st.button("Delete this finding", key=del_key, type="primary"):
                        st.session_state[confirm_key] = True

                    if st.session_state.get(confirm_key, False):
                        st.warning("Soft-delete this finding?")
                        c1, c2, _ = st.columns([1, 1, 4])
                        with c1:
                            if st.button("Confirm", key=f"detail_yes_{i}", type="primary"):
                                soft_delete_batch([{
                                    "_doc_id": row["_doc_id"],
                                    "_source": row["_source"],
                                    "_art_key": row["_art_key"],
                                    "_section_idx": row["_section_idx"],
                                    "category": row.get("category", ""),
                                    "sub_bucket": row.get("sub_bucket", ""),
                                    "sentence": row.get("sentence", ""),
                                }])
                                st.session_state[confirm_key] = False
                                st.session_state["detail_action"] = "Soft-deleted 1 finding."
                                st.cache_data.clear()
                                st.rerun()
                        with c2:
                            if st.button("Cancel", key=f"detail_no_{i}"):
                                st.session_state[confirm_key] = False
                                st.rerun()


# ========================== TAB: ADD GROUND TRUTH ==========================
with tab_add:
    st.info(
        "Coming soon — manually add ground truth findings that the API missed. "
        "This completes the Silver-to-Gold workflow: corrections + additions."
    )
    with st.form("add_ground_truth", clear_on_submit=True):
        st.text_input("Sentence", disabled=True)
        st.selectbox("Category", list(CATEGORY_THEMES.keys()), disabled=True)
        st.number_input("Page Number", min_value=1, disabled=True)
        st.text_area("Observations", disabled=True)
        st.form_submit_button("Add Entry (Coming Soon)", disabled=True)
