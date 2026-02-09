import os
import re
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
    page_title="PO2 Golden Dataset Admin",
    page_icon="\U0001f947",
    layout="wide",
)

# Compact layout CSS
st.markdown(
    """<style>
    .block-container { padding-top: 2.5rem; padding-bottom: 0; }
    [data-testid="stExpander"] { margin-bottom: 0.3rem; }
    [data-testid="stExpander"] details summary { padding: 0.4rem 0.6rem; }
    [data-testid="stVerticalBlock"] > div { gap: 0.3rem; }
    div[data-testid="stDataFrame"] { margin-bottom: 0; }
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

tc_options = sorted(
    set(r.get("tc_number", "") for r in golden_records),
    key=tc_sort_key,
)

st.markdown("### PO2 Golden Dataset Admin")

selected_tcs = st.pills(
    "Test Cases", tc_options, selection_mode="multi",
)

if st.button("Refresh", key="refresh_btn"):
    st.cache_data.clear()
    for key in list(st.session_state.keys()):
        if key.startswith("golden_editor"):
            del st.session_state[key]
    st.rerun()

st.caption(
    "**Silver-to-Gold Curation** — Review findings, delete incorrect ones, "
    "keep what becomes your **golden dataset**. "
    f"Active: **{', '.join(ACTIVE_THEMES)}**"
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

# --- Soft-delete filtering: hide findings that were previously deleted ---
deletion_keys = load_deletion_keys()
total_before_filter = len(df)

if deletion_keys:
    mask = df.apply(
        lambda r: (r["_doc_id"], r["_source"], r["_art_key"], r["_section_idx"]) not in deletion_keys,
        axis=1,
    )
    df = df[mask].reset_index(drop=True)

total = len(df)
deleted_count = get_deletion_count()

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

st.markdown(
    f"#### {'  ·  '.join(banner_parts)}  ·  "
    f"{total} findings"
    + (f"  ·  :red[{deleted_count} deleted]" if deleted_count else "")
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
# Category lock status
# ---------------------------------------------------------------------------
cat_statuses = get_category_statuses()

# ---------------------------------------------------------------------------
# Tabs: Curate | Browse Details | Add Ground Truth
# ---------------------------------------------------------------------------
tab_curate, tab_details, tab_add = st.tabs(["Curate", "Browse Details", "Add Ground Truth"])

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

    display_df.insert(0, "delete", False)

    visible_cols = ["delete", "page", "sentence"]

    col_config = {
        "delete": st.column_config.CheckboxColumn("Delete", width="small"),
        "page": st.column_config.NumberColumn("Pg", width="small", disabled=True),
        "sentence": st.column_config.TextColumn("Flagged Text", width="large", disabled=True),
    }

    if "last_action" in st.session_state:
        st.success(st.session_state.pop("last_action"))

    theme_list = sorted(display_df["theme"].unique(), key=lambda t: THEME_ORDER.get(t, 99))

    for theme in theme_list:
        theme_df = display_df[display_df["theme"] == theme]
        count_in_theme = len(theme_df)

        # Get the category name for this theme (for lock status)
        theme_category = [c for c, t in CATEGORY_THEMES.items() if t == theme]
        cat_name = theme_category[0] if theme_category else theme
        cat_status = cat_statuses.get(cat_name, {})
        is_locked = cat_status.get("status") == "locked"

        sb_summary = theme_df.groupby("sub_bucket").size().sort_values(ascending=False)

        lock_icon = "🔒 " if is_locked else ""
        with st.expander(f"{lock_icon}**{theme}** — {count_in_theme} findings", expanded=False):

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

                    # Colored header + CSS to tint the data editor rows
                    st.markdown(
                        f"<div class='{safe_id}' style='border-left:4px solid {sb_color}; "
                        f"background:{sb_color}22; padding:4px 10px; "
                        f"margin:4px 0 0 0; border-radius:3px 3px 0 0;'>"
                        f"<b>{sb_name}</b> &nbsp;({len(sb_df)})</div>"
                        f"<style>"
                        f".{safe_id} + div [data-testid='stDataFrame'] {{"
                        f"  background: {sb_color}0d; border-left: 3px solid {sb_color};"
                        f"  border-radius: 0 0 4px 4px;"
                        f"}}"
                        f"</style>",
                        unsafe_allow_html=True,
                    )

                    editor_key = f"golden_editor_{theme}_{sb_name}"

                    if is_locked:
                        # Locked: read-only, no delete checkbox
                        st.dataframe(
                            sb_df[["page", "sentence"]],
                            use_container_width=True,
                            hide_index=True,
                        )
                        all_theme_edited.append((sb_df, None))
                    else:
                        edited = st.data_editor(
                            sb_df[visible_cols],
                            use_container_width=True,
                            hide_index=True,
                            num_rows="fixed",
                            key=editor_key,
                            column_config=col_config,
                        )
                        all_theme_edited.append((sb_df, edited))

                    # Detail popovers with delete + confirmation
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
                        })

                    detail_cols = st.columns(min(len(sb_rows), 6))
                    for j, rd in enumerate(sb_rows):
                        pop_key = f"pop_{theme}_{sb_name}_{j}"
                        confirm_key = f"pop_confirm_{pop_key}"
                        with detail_cols[j % len(detail_cols)]:
                            with st.popover(f"Pg {rd['page']}", use_container_width=True):
                                st.markdown(f"**Rule:** {rd['rule_citation']}")
                                st.markdown(f"**Observations:** {rd['observations']}")
                                st.markdown(f"**Recommendations:** {rd['recommendations']}")
                                st.markdown(f"**Summary:** {rd['summary']}")
                                st.divider()
                                if is_locked:
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

            # Bulk delete via checkboxes (only if not locked)
            if not is_locked:
                theme_marked_indices = []
                for sb_df, edited in all_theme_edited:
                    if edited is not None:
                        marked = edited[edited["delete"] == True]
                        theme_marked_indices.extend(marked.index.tolist())

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
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**{tc_label}** — {tc_desc}")
                st.markdown(f"**Page:** {row['page']}")
                st.markdown(f"**Category:** {row['category']}")
                st.markdown(f"**Sub-Bucket:** {row.get('sub_bucket', '')}")
                st.markdown(f"**Rule:** {row['rule_citation']}")
            with col_b:
                st.markdown(f"**Observations:** {row['observations']}")
                st.markdown(f"**Recommendations:** {row['recommendations']}")
                st.markdown(f"**Summary:** {row['summary']}")
                if row.get("reject_reason"):
                    st.markdown(f"**Reject Reason:** :red[{row['reject_reason']}]")

            del_key = f"detail_del_{i}"
            confirm_key = f"detail_confirm_{i}"

            # Check if this category is locked
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
