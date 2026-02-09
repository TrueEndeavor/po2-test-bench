import json
from datetime import datetime
from collections import Counter

import pandas as pd
import streamlit as st

from modules.config import THEME_ORDER, REVIEW_FIELDS, CATEGORY_THEMES
from modules.parsers import cat_to_theme, parse_findings_summary, extract_findings_for_review
from modules.db import get_results_collection, save_all_changes
from modules.api import submit_from_mongo, submit_document
from modules.naming import short_name


def render_metrics_bar(doc_count, results):
    """Top-level pass/fail/pending metrics."""
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


def render_tc_buttons(items, results, use_mongo):
    """Left pane: clickable test-case buttons that trigger API submission."""
    st.markdown("#### Test Cases")

    if st.button("Clear Results", use_container_width=True):
        st.session_state["results"] = {}
        st.session_state.pop("drill_level", None)
        st.session_state.pop("drill_theme", None)
        st.rerun()

    st.markdown("")

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

                    success = resp.status_code == 200
                    full_response = {}
                    mongo_doc_id = None

                    if success:
                        full_response = json.loads(resp.text)
                        # Find the doc in PO2_testing for write-back
                        doc_name = meta["document_metadata"]["document_name"]
                        coll = get_results_collection()
                        mongo_doc = coll.find_one(
                            {"metadata.others.document_metadata.document_name": doc_name},
                            sort=[("created_at", -1)],
                        )
                        if mongo_doc:
                            mongo_doc_id = str(mongo_doc["_id"])

                    st.session_state["results"][name] = {
                        "status_code": resp.status_code,
                        "success": success,
                        "response": resp.text[:5000],
                        "findings": parse_findings_summary(resp.text) if success else {},
                        "full_response": full_response,
                        "mongo_doc_id": mongo_doc_id,
                        "timestamp": datetime.now().isoformat(),
                    }
                except Exception as e:
                    st.session_state["results"][name] = {
                        "status_code": 0,
                        "success": False,
                        "response": str(e),
                        "findings": {},
                        "full_response": {},
                        "mongo_doc_id": None,
                        "timestamp": datetime.now().isoformat(),
                    }
            st.rerun()


def render_drilldown_panel(results, items, use_mongo):
    """Right pane: summary or theme drill-down with editable review."""
    if not results:
        st.markdown("#### Results Dashboard")
        st.info("Run a test case to see results here.")
        return

    drill_level = st.session_state.get("drill_level", "summary")

    if drill_level == "theme":
        _render_theme_level(results, items, use_mongo)
    else:
        _render_summary_level(results, items, use_mongo)


# ---------------------------------------------------------------------------
# Level 1: Summary
# ---------------------------------------------------------------------------
def _render_summary_level(results, items, use_mongo):
    st.markdown("#### Results Dashboard")

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

        st.markdown("**By Theme** *(click View to drill down)*")
        sorted_themes = sorted(all_themes.items(), key=lambda x: THEME_ORDER.get(x[0], 99))
        for theme, count in sorted_themes:
            pct = count / total_findings * 100
            col_bar, col_btn = st.columns([5, 1])
            with col_bar:
                st.progress(pct / 100, text=f"{theme}: **{count}** ({pct:.0f}%)")
            with col_btn:
                if st.button("View", key=f"drill_{theme}"):
                    st.session_state["drill_level"] = "theme"
                    st.session_state["drill_theme"] = theme
                    st.rerun()

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
            tc_themes = Counter()
            for cat, n in r["findings"].items():
                tc_themes[cat_to_theme(cat)] += n
            top = ", ".join(
                f"{t}({n})" for t, n in
                sorted(tc_themes.items(), key=lambda x: THEME_ORDER.get(x[0], 99))[:3]
            )
            st.caption(f"\u2705 **{tc}**: {count} findings \u2014 {top}")
        elif r["success"]:
            st.caption(f"\u2705 **{tc}**: No findings")
        else:
            st.caption(f"\u274c **{tc}**: Failed ({r['status_code']})")


# ---------------------------------------------------------------------------
# Level 2: Theme drill-down with editable accept/reject
# ---------------------------------------------------------------------------
def _render_theme_level(results, items, use_mongo):
    theme = st.session_state.get("drill_theme", "")

    col_back, col_title = st.columns([1, 5])
    with col_back:
        if st.button("\u2190 Back"):
            st.session_state["drill_level"] = "summary"
            st.rerun()
    with col_title:
        st.subheader(theme)

    # Collect all findings matching this theme across all TCs
    all_rows = []
    for item in items:
        name = item["filename"] if use_mongo else item.name
        r = results.get(name)
        if not r or not r.get("success"):
            continue

        tc_label, _ = short_name(name)
        full_resp = r.get("full_response", {})
        mongo_doc_id = r.get("mongo_doc_id")

        if not full_resp or not mongo_doc_id:
            continue

        # Try raw_output first, then sequential_reasoner
        for source_key in ["raw_output", "sequential_reasoner"]:
            source_data = full_resp.get(source_key, {})
            if isinstance(source_data, dict) and source_data:
                findings = extract_findings_for_review(mongo_doc_id, source_data, source_key)
                if findings:
                    for f in findings:
                        if cat_to_theme(f["category"]) == theme:
                            f["test_case"] = tc_label
                            all_rows.append(f)
                    break

    if not all_rows:
        st.info("No findings for this theme.")
        return

    df = pd.DataFrame(all_rows)

    st.caption(f"{len(df)} findings across {df['test_case'].nunique()} test case(s)")

    # Editable table
    visible_cols = [
        "test_case", "sentence", "page", "rule_citation",
        "accept", "accept_with_changes", "reject", "reject_reason",
    ]

    editor_key = f"editor_{theme}"

    edited_df = st.data_editor(
        df[visible_cols],
        use_container_width=True,
        hide_index=True,
        height=400,
        num_rows="fixed",
        key=editor_key,
        column_config={
            "test_case": st.column_config.TextColumn("TC", width="small", disabled=True),
            "sentence": st.column_config.TextColumn("Flagged Text", width="large", disabled=True),
            "page": st.column_config.TextColumn("Pg", width="small", disabled=True),
            "rule_citation": st.column_config.TextColumn("Rule", width="medium", disabled=True),
            "accept": st.column_config.CheckboxColumn("Accept", width="small"),
            "accept_with_changes": st.column_config.CheckboxColumn("Accept w/", width="small"),
            "reject": st.column_config.CheckboxColumn("Reject", width="small"),
            "reject_reason": st.column_config.TextColumn("Reject Reason", width="medium"),
        },
    )

    # Save button
    if st.button("Save Changes", type="primary"):
        save_df = df.copy()
        for f in REVIEW_FIELDS:
            save_df[f] = edited_df[f].values
        count = save_all_changes(df, save_df)
        if count > 0:
            st.success(f"Saved {count} finding(s) to database.")
        else:
            st.info("No changes detected.")

    # Expandable detail per finding
    st.divider()
    st.markdown("**Finding Details**")
    for i, row in df.iterrows():
        status = (
            "Accepted" if row["accept"]
            else "Rejected" if row["reject"]
            else "Accept w/ Changes" if row["accept_with_changes"]
            else "Unreviewed"
        )
        color = {"Accepted": "green", "Rejected": "red",
                 "Accept w/ Changes": "orange", "Unreviewed": "gray"}[status]
        preview = str(row["sentence"])[:70]
        label = f":{color}[**{status}**] {row.get('test_case', '')} \u2014 {preview}"

        with st.expander(label, expanded=False):
            ca, cb = st.columns(2)
            with ca:
                st.markdown(f"**Artifact:** {row['artifact_type']}")
                st.markdown(f"**Page:** {row['page']}")
                st.markdown(f"**Category:** {row['category']}")
                st.markdown(f"**Rule:** {row['rule_citation']}")
            with cb:
                st.markdown(f"**Observations:** {row['observations']}")
                st.markdown(f"**Recommendations:** {row['recommendations']}")
                st.markdown(f"**Summary:** {row['summary']}")
                if row["reject_reason"]:
                    st.markdown(f"**Reject Reason:** :red[{row['reject_reason']}]")
