"""
Runs Dashboard - View all test runs and their confusion matrices
"""
import streamlit as st
import pandas as pd
from datetime import datetime
from bson import ObjectId
from modules.db import get_runs_collection
from modules.run_names import parse_run_name

st.set_page_config(
    page_title="Runs Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("Runs Dashboard")
st.caption("Track all test runs and compare performance")

# Load all runs from MongoDB
@st.cache_data(ttl=60)
def load_runs():
    """Load all runs from MongoDB."""
    coll = get_runs_collection()
    runs = list(coll.find().sort("timestamp", -1))  # Most recent first
    return runs

runs = load_runs()

if not runs:
    st.info("No test runs found. Run some test cases from the main page to see them here!")
    st.stop()

# ---------------------------------------------------------------------------
# Run Log Table (no scroll — sized to fit)
# ---------------------------------------------------------------------------
st.markdown(f"### Run Log ({len(runs)} runs)")

rows = []
for run in runs:
    run_info = parse_run_name(run.get("run_name", "Unknown"))
    metrics = run.get("metrics", {})

    rows.append({
        "Run Name": run_info["display_name"],
        "Date": run_info["date_str"],
        "Time": run_info["time_str"],
        "Run By": run.get("run_by", ""),
        "Prompt Change": run.get("prompt_label", ""),
        "TCs": run.get("test_cases_run", 0),
        "TP": metrics.get("tp", 0),
        "FP": metrics.get("fp", 0),
        "FN": metrics.get("fn", 0),
        "Precision": f"{metrics.get('precision', 0):.1%}" if metrics.get('precision') else "-",
        "Recall": f"{metrics.get('recall', 0):.1%}" if metrics.get('recall') else "-",
    })

df = pd.DataFrame(rows)
df.index = df.index + 1  # 1-based index

st.table(df)

# ---------------------------------------------------------------------------
# Delete Runs
# ---------------------------------------------------------------------------
with st.expander("Delete Runs"):
    run_options = {
        f"{parse_run_name(r.get('run_name', 'Unknown'))['display_name']}  —  {r.get('test_cases_run', 0)} TCs, "
        f"F1: {r.get('metrics', {}).get('f1', 0):.1%}": str(r["_id"])
        for r in runs
    }
    selected = st.multiselect(
        "Select runs to delete",
        options=list(run_options.keys()),
        placeholder="Choose one or more runs...",
    )
    if selected:
        if "confirm_delete_runs" not in st.session_state:
            st.session_state.confirm_delete_runs = False

        if not st.session_state.confirm_delete_runs:
            if st.button(f"Delete {len(selected)} run(s)", type="secondary"):
                st.session_state.confirm_delete_runs = True
                st.rerun()
        else:
            st.warning(f"Are you sure you want to delete **{len(selected)} run(s)**? This cannot be undone.")
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("Yes, Delete", type="primary"):
                    coll = get_runs_collection()
                    for label in selected:
                        coll.delete_one({"_id": ObjectId(run_options[label])})
                    st.session_state.confirm_delete_runs = False
                    st.cache_data.clear()
                    st.rerun()
            with col_no:
                if st.button("Cancel"):
                    st.session_state.confirm_delete_runs = False
                    st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Per-Run Details (tabs)
# ---------------------------------------------------------------------------
st.markdown("### Run Details")

if len(runs) > 0:
    tab_names = [parse_run_name(run.get("run_name", "Unknown"))["display_name"] for run in runs]
    tabs = st.tabs(tab_names[:10])

    for idx, (tab, run) in enumerate(zip(tabs, runs[:10])):
        with tab:
          try:
            run_info = parse_run_name(run.get("run_name", "Unknown"))
            metrics = run.get("metrics", {})
            tp = metrics.get("tp", 0) or 0
            fp = metrics.get("fp", 0) or 0
            fn = metrics.get("fn", 0) or 0
            precision = metrics.get("precision", 0) or 0
            recall = metrics.get("recall", 0) or 0
            f1 = metrics.get("f1", 0) or 0

            # --- Colored confusion summary ---
            st.markdown(
                f"""
                <div style="display:flex; gap:12px; margin-bottom:16px;">
                    <div style="background:#e6f4ea; border-left:4px solid #34a853; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">True Positives</div>
                        <div style="font-size:28px; font-weight:700; color:#1e7e34;">{tp}</div>
                    </div>
                    <div style="background:#fce8e6; border-left:4px solid #ea4335; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">False Positives</div>
                        <div style="font-size:28px; font-weight:700; color:#c5221f;">{fp}</div>
                    </div>
                    <div style="background:#fff3e0; border-left:4px solid #f9ab00; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">False Negatives</div>
                        <div style="font-size:28px; font-weight:700; color:#e37400;">{fn}</div>
                    </div>
                    <div style="background:#e8f0fe; border-left:4px solid #4285f4; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">Precision</div>
                        <div style="font-size:28px; font-weight:700; color:#1a73e8;">{precision:.1%}</div>
                    </div>
                    <div style="background:#e8f0fe; border-left:4px solid #4285f4; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">Recall</div>
                        <div style="font-size:28px; font-weight:700; color:#1a73e8;">{recall:.1%}</div>
                    </div>
                    <div style="background:#e8f0fe; border-left:4px solid #4285f4; padding:12px 20px; border-radius:4px; flex:1; text-align:center;">
                        <div style="font-size:13px; color:#555;">F1 Score</div>
                        <div style="font-size:28px; font-weight:700; color:#1a73e8;">{f1:.1%}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # --- Per-theme breakdown ---
            per_theme = run.get("per_theme_metrics", {})
            if per_theme:
                st.markdown("#### By Theme")
                theme_rows = []
                for theme in sorted(per_theme.keys()):
                    t = per_theme[theme]
                    t_found = (t.get("tp", 0) or 0) + (t.get("fp", 0) or 0)
                    theme_rows.append({
                        "Theme": theme,
                        "GT Expected": t.get("expected", 0) or 0,
                        "API Found": t_found,
                        "TP": t.get("tp", 0) or 0,
                        "FP": t.get("fp", 0) or 0,
                        "FN": t.get("fn", 0) or 0,
                        "Precision": f"{(t.get('precision', 0) or 0):.1%}",
                        "Recall": f"{(t.get('recall', 0) or 0):.1%}",
                        "F1": f"{(t.get('f1', 0) or 0):.1%}",
                    })
                st.dataframe(
                    pd.DataFrame(theme_rows),
                    use_container_width=True, hide_index=True,
                )

            # --- Per-TC breakdown with expandable findings ---
            if run.get("per_tc_metrics"):
                st.markdown("#### Per Test Case")

                for tc_name in sorted(run["per_tc_metrics"].keys()):
                    tc_m = run["per_tc_metrics"][tc_name]
                    tc_tp = tc_m.get("tp", 0) or 0
                    tc_fp = tc_m.get("fp", 0) or 0
                    tc_fn = tc_m.get("fn", 0) or 0
                    tc_found = tc_m.get("relevant_found", tc_tp + tc_fp) or 0
                    tc_unscored = tc_m.get("unscored", 0) or 0
                    label = f"{tc_name} — Expected: {tc_m.get('expected', 0) or 0} | Found: {tc_found} | TP: {tc_tp} | FP: {tc_fp} | FN: {tc_fn}"
                    if tc_unscored:
                        label += f" | Unscored: {tc_unscored}"

                    with st.expander(label, expanded=False):
                        # Per-theme for this TC
                        tc_themes = tc_m.get("per_theme", {})
                        if tc_themes:
                            t_rows = []
                            for theme in sorted(tc_themes.keys()):
                                tm = tc_themes[theme]
                                t_rows.append({
                                    "Theme": theme,
                                    "Expected": tm.get("expected", 0) or 0,
                                    "Found": tm.get("found", 0) or 0,
                                    "TP": tm.get("tp", 0) or 0,
                                    "FP": tm.get("fp", 0) or 0,
                                    "FN": tm.get("fn", 0) or 0,
                                })
                            st.dataframe(
                                pd.DataFrame(t_rows),
                                use_container_width=True, hide_index=True,
                            )

                        # Detailed findings
                        findings = tc_m.get("findings", [])
                        if findings:
                            st.markdown("**Findings**")
                            st.dataframe(
                                pd.DataFrame(findings),
                                use_container_width=True, hide_index=True,
                                column_config={
                                    "gt_status": st.column_config.TextColumn("GT", width="small"),
                                    "theme": st.column_config.TextColumn("Theme", width="small"),
                                    "page": st.column_config.TextColumn("Page", width="small"),
                                    "sentence": st.column_config.TextColumn("Sentence", width="large"),
                                    "category": st.column_config.TextColumn("Category", width="medium"),
                                },
                            )
                        elif tc_found == 0:
                            st.caption("No findings for this test case.")

                        # Fallback for old runs without detailed data
                        if not tc_themes and not findings and tc_found > 0:
                            st.caption("Detailed per-theme and findings data not available for this run. Re-run the test case to populate.")
                            st.markdown(f"**Expected:** {tc_m.get('expected', 0)} &nbsp; **Found:** {tc_found} &nbsp; **TP:** {tc_tp} &nbsp; **FP:** {tc_fp} &nbsp; **FN:** {tc_fn}")

          except Exception as e:
            st.error(f"Error rendering run: {e}")


# ---------------------------------------------------------------------------
# Run Comparison
# ---------------------------------------------------------------------------
st.divider()
st.markdown("### Run Comparison")

if len(runs) >= 2:
    # Build dropdown labels with prompt + date context
    def _run_label(r):
        info = parse_run_name(r.get("run_name", "Unknown"))
        prompt = r.get("prompt_label", "")
        suffix = f"  [{prompt}]" if prompt else ""
        return f"{info['display_name']}  ({info['date_str']}){suffix}"

    run_labels = [_run_label(r) for r in runs]

    col1, col2 = st.columns(2)
    with col1:
        idx2 = st.selectbox("Baseline Run", range(len(run_labels)),
                            format_func=lambda i: run_labels[i], key="run2",
                            index=min(1, len(run_labels) - 1))
    with col2:
        idx1 = st.selectbox("Current Run", range(len(run_labels)),
                            format_func=lambda i: run_labels[i], key="run1")

    run1 = runs[idx1]
    run2 = runs[idx2]
    m1 = run1.get("metrics", {})
    m2 = run2.get("metrics", {})

    st.caption("Green = improved vs baseline, Red = worse vs baseline")

    col1, col2, col3 = st.columns(3)
    col1.metric("TP (higher is better)", m1.get("tp", 0),
                delta=f"{m1.get('tp', 0) - m2.get('tp', 0):+d} vs baseline")
    col2.metric("FP (lower is better)", m1.get("fp", 0),
                delta=f"{m1.get('fp', 0) - m2.get('fp', 0):+d} vs baseline",
                delta_color="inverse")
    col3.metric("FN (lower is better)", m1.get("fn", 0),
                delta=f"{m1.get('fn', 0) - m2.get('fn', 0):+d} vs baseline",
                delta_color="inverse")

    col4, col5, col6 = st.columns(3)

    p1 = m1.get("precision", 0) or 0
    p2 = m2.get("precision", 0) or 0
    col4.metric("Precision", f"{p1:.1%}",
                delta=f"{(p1-p2):+.1%} vs baseline")

    r1 = m1.get("recall", 0) or 0
    r2 = m2.get("recall", 0) or 0
    col5.metric("Recall", f"{r1:.1%}",
                delta=f"{(r1-r2):+.1%} vs baseline")

    f1_1 = m1.get("f1", 0) or 0
    f1_2 = m2.get("f1", 0) or 0
    col6.metric("F1 Score", f"{f1_1:.1%}",
                delta=f"{(f1_1-f1_2):+.1%} vs baseline")

    # Winner summary
    if f1_1 > f1_2:
        st.success(f"Current run wins — F1: {f1_1:.1%} vs baseline {f1_2:.1%}")
    elif f1_1 < f1_2:
        st.error(f"Baseline wins — F1: {f1_2:.1%} vs current {f1_1:.1%}")
    else:
        st.info(f"Tied on F1: {f1_1:.1%}")
else:
    st.info("Run more test cases to enable comparison!")

# Refresh button
if st.button("Refresh Data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
