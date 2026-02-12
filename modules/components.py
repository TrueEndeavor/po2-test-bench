import json
from datetime import datetime
from collections import Counter

import pandas as pd
import streamlit as st

from modules.config import REVIEW_FIELDS
from modules.parsers import parse_findings_summary, extract_findings_for_review
from modules.db import get_results_collection, save_all_changes, save_run
from modules.api import submit_from_mongo, submit_document
from modules.naming import short_name
from modules.ground_truth import calculate_gt_metrics, get_missing_gt_findings


def _process_single_tc(item, use_mongo, gt_keys, gt_df, run_name):
    """Run a single TC through the API and return the result dict.

    This is the shared processing logic used by both individual TC buttons
    and the Run All batch mode.
    """
    name = item["filename"] if use_mongo else item.name
    tc, _ = short_name(name)

    if use_mongo:
        resp, meta = submit_from_mongo(item)
    else:
        resp, meta = submit_document(item)

    success = resp.status_code == 200
    full_response = {}
    mongo_doc_id = None
    gt_metrics = None

    if success:
        full_response = json.loads(resp.text)
        doc_name = meta["document_metadata"]["document_name"]
        coll = get_results_collection()
        mongo_doc = coll.find_one(
            {"metadata.others.document_metadata.document_name": doc_name},
            sort=[("created_at", -1)],
        )
        if mongo_doc:
            mongo_doc_id = str(mongo_doc["_id"])

        if gt_keys is not None and gt_df is not None and not gt_df.empty:
            findings_list = []
            for source_key in ["raw_output", "sequential_reasoner"]:
                source_data = full_response.get(source_key, {})
                if isinstance(source_data, dict) and source_data:
                    findings = extract_findings_for_review(
                        mongo_doc_id or "temp", source_data, source_key
                    )
                    if findings:
                        findings_list = findings
                        break

            if findings_list:
                gt_metrics = calculate_gt_metrics(findings_list, gt_keys, gt_df, tc)

    return {
        "status_code": resp.status_code,
        "success": success,
        "response": resp.text[:5000],
        "findings": parse_findings_summary(resp.text) if success else {},
        "full_response": full_response,
        "mongo_doc_id": mongo_doc_id,
        "gt_metrics": gt_metrics,
        "run_name": run_name or "unknown",
        "timestamp": datetime.now().isoformat(),
    }


def render_tc_buttons(items, results, use_mongo, gt_keys=None, gt_df=None, run_name=None,
                      prompt_label="", run_by=""):
    """Left pane: clickable test-case buttons that trigger API submission."""
    st.markdown("#### Test Cases")

    # --- Action buttons row ---
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        run_all_clicked = st.button(
            "▶ Run All", type="primary", use_container_width=True,
            help="Run all pending test cases sequentially",
        )
    with btn_col2:
        if st.button("Clear Results", use_container_width=True):
            st.session_state["results"] = {}
            st.session_state.pop("drill_level", None)
            st.session_state.pop("drill_category", None)
            st.rerun()

    # --- Run All batch mode (runs synchronously on button click) ---
    if run_all_clicked:
        pending = [
            item for item in items
            if (item["filename"] if use_mongo else item.name) not in results
        ]

        if not pending:
            st.success("All test cases already run!")
        else:
            total = len(items)
            already_done = total - len(pending)
            progress_bar = st.progress(
                already_done / total,
                text=f"Progress: {already_done}/{total} completed",
            )
            status_text = st.empty()

            for i, item in enumerate(pending):
                name = item["filename"] if use_mongo else item.name
                tc, desc = short_name(name)
                status_text.info(f"Running **{tc}** — {desc} ({already_done + i + 1}/{total})...")

                try:
                    with st.spinner(f"Waiting for API response for {tc}..."):
                        result = _process_single_tc(item, use_mongo, gt_keys, gt_df, run_name)
                    st.session_state["results"][name] = result
                except Exception as e:
                    st.session_state["results"][name] = {
                        "status_code": 0,
                        "success": False,
                        "response": str(e),
                        "findings": {},
                        "full_response": {},
                        "mongo_doc_id": None,
                        "gt_metrics": None,
                        "run_name": run_name or "unknown",
                        "timestamp": datetime.now().isoformat(),
                    }

                # Persist after each TC
                save_run(run_name or "unknown", st.session_state["results"],
                        prompt_label, run_by)

                done_count = already_done + i + 1
                progress_bar.progress(
                    done_count / total,
                    text=f"Progress: {done_count}/{total} completed",
                )

            status_text.success(f"Done! All {total} test cases completed.")
            st.rerun()

    st.markdown("")

    # --- Individual TC buttons (compact grid) ---
    cols = st.columns(6)
    for idx, item in enumerate(items):
        name = item["filename"] if use_mongo else item.name
        tc, desc = short_name(name)
        result = results.get(name)

        if result:
            icon = "\u2705" if result["success"] else "\u274c"
        else:
            icon = "\u23f3"

        with cols[idx % 6]:
            if st.button(f"{icon} {tc}", key=f"run_{name}", use_container_width=True):
                with st.spinner(f"Running {tc}..."):
                    try:
                        result = _process_single_tc(item, use_mongo, gt_keys, gt_df, run_name)
                        st.session_state["results"][name] = result
                    except Exception as e:
                        st.session_state["results"][name] = {
                            "status_code": 0,
                            "success": False,
                            "response": str(e),
                            "findings": {},
                            "full_response": {},
                            "mongo_doc_id": None,
                            "gt_metrics": None,
                            "run_name": run_name or "unknown",
                            "timestamp": datetime.now().isoformat(),
                        }
                    save_run(run_name or "unknown", st.session_state["results"],
                             prompt_label, run_by)
                st.rerun()


def render_drilldown_panel(results, items, use_mongo, gt_keys=None, gt_df=None):
    """Right pane: summary or theme drill-down with editable review."""
    if not results:
        st.markdown("#### Results Dashboard")
        st.info("Run a test case to see results here.")
        return

    drill_level = st.session_state.get("drill_level", "summary")

    if drill_level == "category":
        _render_category_level(results, items, use_mongo)
    elif drill_level == "gt_comparison":
        _render_gt_comparison(results, items, use_mongo, gt_keys, gt_df)
    else:
        _render_summary_level(results, items, use_mongo, gt_keys, gt_df)


# ---------------------------------------------------------------------------
# Level 1: Summary
# ---------------------------------------------------------------------------
def _render_summary_level(results, items, use_mongo, gt_keys=None, gt_df=None):
    st.markdown("#### Results Dashboard")

    all_categories = Counter()
    total_findings = 0
    has_gt = gt_keys is not None and gt_df is not None and not gt_df.empty

    # Aggregate GT metrics across all test cases
    agg_gt = {
        "tp": 0, "partial_tp": 0.0, "fp": 0, "fn": 0,
        "suppressed": 0, "weighted_tp": 0.0,
        "expected": 0, "found": 0, "relevant_found": 0
    }

    for r in results.values():
        if r.get("success") and r.get("findings"):
            for cat, count in r["findings"].items():
                all_categories[cat] += count
                total_findings += count

        # Aggregate GT metrics
        if has_gt and r.get("gt_metrics"):
            metrics = r["gt_metrics"]
            tp = metrics.get("tp", 0)
            fp = metrics.get("fp", 0)
            agg_gt["tp"] += tp
            agg_gt["partial_tp"] += metrics.get("partial_tp", 0.0)
            agg_gt["fp"] += fp
            agg_gt["fn"] += metrics.get("fn", 0)
            agg_gt["suppressed"] += metrics.get("suppressed", 0)
            agg_gt["weighted_tp"] += metrics.get("weighted_tp", 0.0)
            agg_gt["expected"] += metrics.get("expected", 0)
            agg_gt["found"] += metrics.get("found", 0)
            agg_gt["relevant_found"] += metrics.get("relevant_found", tp + fp)

    # Display GT metrics summary
    if has_gt and agg_gt["expected"] > 0:
        st.markdown("### Ground Truth Comparison (All Categories)")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("GT Expected", agg_gt["expected"], help="Total findings in ground truth")
        col2.metric("API Found", agg_gt["relevant_found"], help="Findings from GT theme")
        col3.metric("Exact Matches (TP)", agg_gt["tp"], help="Exact sentence match")
        col4.metric("False Positives", agg_gt["fp"], help="Wrong page/context")

        col5, col6, _, _ = st.columns(4)
        col5.metric("False Negatives", agg_gt["fn"], help="In GT but NOT found by API")

        if st.button("📊 View Detailed GT Comparison", use_container_width=True):
            st.session_state["drill_level"] = "gt_comparison"
            st.rerun()

        st.divider()

    if total_findings > 0:
        st.markdown("### Findings by Category")
        st.metric("Total Findings", total_findings)

        st.markdown("**By Category** *(click View to drill down)*")
        sorted_cats = sorted(all_categories.items(), key=lambda x: (-x[1], x[0]))
        for cat, count in sorted_cats:
            pct = count / total_findings * 100
            col_bar, col_btn = st.columns([5, 1])
            with col_bar:
                st.progress(pct / 100, text=f"{cat}: **{count}** ({pct:.0f}%)")
            with col_btn:
                if st.button("View", key=f"drill_{cat}"):
                    st.session_state["drill_level"] = "category"
                    st.session_state["drill_category"] = cat
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
            top = ", ".join(
                f"{c}({n})" for c, n in
                sorted(r["findings"].items(), key=lambda x: -x[1])[:3]
            )

            # Add GT metrics if available
            gt_suffix = ""
            if r.get("gt_metrics"):
                gt_m = r["gt_metrics"]
                gt_suffix = f" | GT: {gt_m['tp']}TP/{gt_m['fp']}FP/{gt_m['fn']}FN"

            st.caption(f"\u2705 **{tc}**: {count} findings \u2014 {top}{gt_suffix}")
        elif r["success"]:
            st.caption(f"\u2705 **{tc}**: No findings")
            # Debug: Show response preview for successful runs with 0 findings
            if st.session_state.get("debug_mode"):
                with st.expander(f"Debug: View API response for {tc}"):
                    st.json(json.loads(r.get("response", "{}"))[:2000] if r.get("response") else {})
        else:
            error_msg = r.get("response", "Unknown error")
            st.caption(f"\u274c **{tc}**: Failed ({r['status_code']})")
            with st.expander(f"View error details for {tc}"):
                st.error(error_msg)


# ---------------------------------------------------------------------------
# Level 2: Category drill-down with editable accept/reject
# ---------------------------------------------------------------------------
def _render_category_level(results, items, use_mongo):
    category = st.session_state.get("drill_category", "")

    col_back, col_title = st.columns([1, 5])
    with col_back:
        if st.button("\u2190 Back"):
            st.session_state["drill_level"] = "summary"
            st.rerun()
    with col_title:
        st.subheader(category)

    # Collect all findings matching this category across all TCs
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
                        if f["category"] == category:
                            f["test_case"] = tc_label
                            all_rows.append(f)
                    break

    if not all_rows:
        st.info("No findings for this category.")
        return

    df = pd.DataFrame(all_rows)

    st.caption(f"{len(df)} findings across {df['test_case'].nunique()} test case(s)")

    # Editable table
    visible_cols = [
        "test_case", "sentence", "page", "rule_citation",
        "accept", "accept_with_changes", "reject", "reject_reason",
    ]

    editor_key = f"editor_{category}"

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


# ---------------------------------------------------------------------------
# Level 3: Ground Truth Comparison
# ---------------------------------------------------------------------------
def _render_gt_comparison(results, items, use_mongo, gt_keys, gt_df):
    """Detailed ground truth comparison view."""
    col_back, col_title = st.columns([1, 5])
    with col_back:
        if st.button("\u2190 Back"):
            st.session_state["drill_level"] = "summary"
            st.rerun()
    with col_title:
        st.subheader("Ground Truth Comparison")

    if not gt_keys or gt_df.empty:
        st.warning("No ground truth data available.")
        return

    # Collect GT comparison data for all test cases
    comparison_rows = []

    for item in items:
        name = item["filename"] if use_mongo else item.name
        tc, desc = short_name(name)
        r = results.get(name)

        if not r or not r.get("success"):
            continue

        gt_metrics = r.get("gt_metrics")
        if not gt_metrics:
            continue

        comparison_rows.append({
            "TC": tc,
            "Description": desc,
            "GT Expected": gt_metrics["expected"],
            "API Found": gt_metrics.get("relevant_found", gt_metrics["found"]),
            "Exact (TP)": gt_metrics["tp"],
            "Partial": f"{gt_metrics.get('partial_tp', 0):.1f}",
            "FP": gt_metrics["fp"],
            "FN": gt_metrics["fn"],
            "Suppressed": gt_metrics.get("suppressed", 0),
        })

    if not comparison_rows:
        st.info("No GT comparison data available. Run test cases to see GT metrics.")
        return

    # Display comparison table
    df_comp = pd.DataFrame(comparison_rows)
    st.markdown("### Summary Table")
    st.dataframe(df_comp, use_container_width=True, hide_index=True)

    # Per-TC detailed view
    st.markdown("---")
    st.markdown("### Per Test Case Details")

    for item in items:
        name = item["filename"] if use_mongo else item.name
        tc, desc = short_name(name)
        r = results.get(name)

        if not r or not r.get("success") or not r.get("gt_metrics"):
            continue

        gt_metrics = r["gt_metrics"]
        detailed_findings = gt_metrics.get("detailed_findings", [])

        with st.expander(f"{tc} — {desc}", expanded=False):
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("GT Expected", gt_metrics["expected"])
            col2.metric("API Found", gt_metrics.get("relevant_found", gt_metrics["found"]))
            col3.metric("Exact Matches", gt_metrics["tp"])
            col4.metric("Partial Matches", f"{gt_metrics.get('partial_tp', 0):.1f}")

            # Show findings with GT status
            if detailed_findings:
                st.markdown("#### API Findings")
                df_findings = pd.DataFrame([
                    {
                        "GT Status": f.get("gt_status", "N/A"),
                        "Page": f.get("page", ""),
                        "Sentence": str(f.get("sentence", ""))[:100],
                        "Category": f.get("category", ""),
                    }
                    for f in detailed_findings
                ])

                # Style TP and FP differently
                st.dataframe(
                    df_findings,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "GT Status": st.column_config.TextColumn("GT Status", width="small"),
                        "Page": st.column_config.TextColumn("Page", width="small"),
                        "Sentence": st.column_config.TextColumn("Sentence", width="large"),
                        "Category": st.column_config.TextColumn("Category", width="medium"),
                    }
                )

            # Show missing GT findings (false negatives)
            if gt_metrics["fn"] > 0:
                st.markdown(f"#### Missing Findings (FN: {gt_metrics['fn']})")
                st.caption("These findings are in the ground truth but were NOT detected by the API:")

                missing = get_missing_gt_findings(gt_df, tc, detailed_findings, gt_keys)
                if missing:
                    df_missing = pd.DataFrame([
                        {
                            "Page": m.get("page", ""),
                            "Sentence": str(m.get("sentence", ""))[:100],
                            "Category": m.get("category", ""),
                            "Sub Bucket": m.get("sub_bucket", ""),
                        }
                        for m in missing
                    ])
                    st.dataframe(df_missing, use_container_width=True, hide_index=True)
