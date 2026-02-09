import os
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="PO2 Test Bench",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# MongoDB connection (cached)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_mongo_client():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        st.error("MONGODB_URI not found in .env")
        st.stop()
    return MongoClient(uri)


@st.cache_data(ttl=300)
def load_data():
    """Load all documents and flatten into usable DataFrames."""
    client = get_mongo_client()
    db = client["PO2xNW"]
    coll = db["PO2_testing"]
    docs = list(coll.find())

    findings_rows = []
    token_rows = []
    sr_rows = []  # sequential reasoner

    artifact_types = [
        "misleading_artifact", "performance_artifact", "disclosure_artifact",
        "testimonial_artifact", "digital_artifact", "comparison_artifact",
        "ranking_artifact", "thirdparty_artifact", "editorial_artifact",
        "typo_artifact",
    ]

    for doc in docs:
        meta = doc.get("metadata", {})
        uuid = meta.get("uuid", str(doc["_id"]))
        doc_meta = meta.get("others", {}).get("document_metadata", {})
        doc_name = doc_meta.get("document_name", "Unknown")
        doc_type = doc_meta.get("document_type", "Unknown")
        created = doc.get("created_at", None)

        # --- Derive a readable label from the first section title ---
        raw = doc.get("raw_output", {})
        first_section = ""
        for _ak in artifact_types:
            _secs = raw.get(_ak, {}).get("sections", []) if isinstance(raw.get(_ak), dict) else []
            if _secs:
                first_section = _secs[0].get("section_title", "")
                break
        # Build a unique, readable display label
        created_str = ""
        if created:
            try:
                created_str = created.strftime("%Y-%m-%d %H:%M")
            except Exception:
                created_str = str(created)[:16]
        hint = first_section[:50] if first_section else "empty"
        doc_label = f"{doc_name} — {hint} ({created_str})"

        # --- Findings from raw_output ---
        for art_key in artifact_types:
            art = raw.get(art_key, {})
            sections = art.get("sections", [])
            for s in sections:
                findings_rows.append({
                    "uuid": uuid,
                    "doc_name": doc_name,
                    "doc_label": doc_label,
                    "doc_type": doc_type,
                    "created_at": created,
                    "artifact_type": art_key.replace("_artifact", ""),
                    "section_title": s.get("section_title", ""),
                    "sentence": s.get("sentence", ""),
                    "page_number": s.get("page_number"),
                    "observations": s.get("observations", ""),
                    "rule_citation": s.get("rule_citation", ""),
                    "recommendations": s.get("recommendations", ""),
                    "category": s.get("category", "N/A"),
                    "sub_bucket": s.get("sub_bucket", "N/A"),
                    "summary": s.get("summary", ""),
                    "accept": s.get("accept", False),
                    "accept_with_changes": s.get("accept_with_changes", False),
                    "reject": s.get("reject", False),
                    "reject_reason": s.get("reject_reason", ""),
                    "source": "raw_output",
                })

        # --- Findings from sequential_reasoner ---
        sr = doc.get("sequential_reasoner", {})
        for art_key in artifact_types:
            art = sr.get(art_key, {})
            if not isinstance(art, dict):
                continue
            sections = art.get("sections", [])
            for s in sections:
                sr_rows.append({
                    "uuid": uuid,
                    "doc_name": doc_name,
                    "doc_label": doc_label,
                    "artifact_type": art_key.replace("_artifact", ""),
                    "section_title": s.get("section_title", ""),
                    "category": s.get("category", "N/A"),
                    "sub_bucket": s.get("sub_bucket", "N/A"),
                    "summary": s.get("summary", ""),
                    "accept": s.get("accept", False),
                    "reject": s.get("reject", False),
                    "source": "sequential_reasoner",
                })

        # --- Token data ---
        token_map = {
            "misleading": "misleading_token_data",
            "performance": "performance_token_data",
            "disclosure": "disclosure_token_data",
            "testimonial": "testimonial_token_data",
            "comparison": "comparision_token_data",  # note: typo in source
            "ranking": "ranking_token_data",
            "thirdparty": "thirdparty_token_data",
            "editorial": "editorial_token_data",
            "typo": "typo_token_data",
        }
        for label, field in token_map.items():
            td = doc.get(field)
            if td:
                token_rows.append({
                    "uuid": uuid,
                    "doc_name": doc_name,
                    "doc_label": doc_label,
                    "created_at": created,
                    "artifact_type": label,
                    "total_tokens": td.get("total_token_count", 0),
                    "prompt_tokens": td.get("prompt_token_count", 0),
                    "thoughts_tokens": td.get("thoughts_token_count", 0),
                    "candidate_tokens": td.get("candidate_token_count", 0),
                })

    df_findings = pd.DataFrame(findings_rows)
    df_tokens = pd.DataFrame(token_rows)
    df_sr = pd.DataFrame(sr_rows)

    return df_findings, df_tokens, df_sr


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
df_findings, df_tokens, df_sr = load_data()

# ---------------------------------------------------------------------------
# Sidebar – filters
# ---------------------------------------------------------------------------
st.sidebar.title("Filters")

if not df_findings.empty:
    # Build label map: uuid -> readable doc_label
    doc_label_map = (
        df_findings.drop_duplicates("uuid")
        .set_index("uuid")["doc_label"]
        .to_dict()
    )
    doc_options = list(doc_label_map.keys())
    selected_docs = st.sidebar.multiselect(
        "Documents",
        options=doc_options,
        default=doc_options,
        format_func=lambda x: doc_label_map.get(x, x),
    )
    selected_artifacts = st.sidebar.multiselect(
        "Artifact Types",
        options=sorted(df_findings["artifact_type"].unique()),
        default=sorted(df_findings["artifact_type"].unique()),
    )
    # Apply filters
    mask = df_findings["uuid"].isin(selected_docs) & df_findings["artifact_type"].isin(selected_artifacts)
    df_f = df_findings[mask].copy()
    df_t = df_tokens[df_tokens["uuid"].isin(selected_docs) & df_tokens["artifact_type"].isin(selected_artifacts)].copy()
    df_s = df_sr[df_sr["uuid"].isin(selected_docs) & df_sr["artifact_type"].isin(selected_artifacts)].copy()
else:
    df_f = df_findings.copy()
    df_t = df_tokens.copy()
    df_s = df_sr.copy()

# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------
st.title("PO2 Test Bench — Review Dashboard")
st.caption("Organized by **Quality** · **Cost** · **Latency**")

# ---------------------------------------------------------------------------
# Top-level axis tabs
# ---------------------------------------------------------------------------
tab_quality, tab_cost, tab_latency, tab_categories = st.tabs([
    "🎯 Quality", "💰 Cost", "⏱️ Latency", "📂 Category Distribution"
])

# =========================================================================
# TAB 1 – QUALITY
# =========================================================================
with tab_quality:
    q_acc, q_con = st.tabs(["Accuracy", "Consistency"])

    # ----- ACCURACY -----
    with q_acc:
        st.subheader("Accuracy — Confusion Matrix")
        st.markdown(
            "**Accept = True Positive** (correctly flagged) · "
            "**Reject = False Positive** (incorrectly flagged) · "
            "**Unreviewed** = pending human review"
        )

        if df_f.empty:
            st.info("No findings data available for the current filters.")
        else:
            # Compute review status
            df_f["status"] = df_f.apply(
                lambda r: "TP (Accepted)" if r["accept"]
                else ("FP (Rejected)" if r["reject"]
                      else ("Accepted w/ Changes" if r["accept_with_changes"]
                            else "Unreviewed")),
                axis=1,
            )

            # --- Drill-down level selector ---
            drill = st.radio(
                "Drill-down level",
                ["Document", "Artifact Type", "Category", "Sub-Bucket"],
                horizontal=True,
                key="accuracy_drill",
            )

            drill_col_map = {
                "Document": "doc_label",
                "Artifact Type": "artifact_type",
                "Category": "category",
                "Sub-Bucket": "sub_bucket",
            }
            group_col = drill_col_map[drill]

            # Pivot for confusion matrix style table
            status_counts = (
                df_f.groupby([group_col, "status"])
                .size()
                .reset_index(name="count")
            )
            pivot = status_counts.pivot_table(
                index=group_col, columns="status", values="count", fill_value=0
            ).reset_index()

            # Ensure all status columns exist
            for col in ["TP (Accepted)", "FP (Rejected)", "Accepted w/ Changes", "Unreviewed"]:
                if col not in pivot.columns:
                    pivot[col] = 0

            # Precision metric
            pivot["Total"] = pivot[["TP (Accepted)", "FP (Rejected)", "Accepted w/ Changes", "Unreviewed"]].sum(axis=1)
            reviewed = pivot["TP (Accepted)"] + pivot["FP (Rejected)"] + pivot["Accepted w/ Changes"]
            pivot["Precision"] = (
                (pivot["TP (Accepted)"] + pivot["Accepted w/ Changes"]) / reviewed
            ).fillna(0).map(lambda x: f"{x:.0%}")

            col1, col2 = st.columns([2, 1])
            with col1:
                # Stacked bar chart
                fig = px.bar(
                    status_counts,
                    x=group_col,
                    y="count",
                    color="status",
                    color_discrete_map={
                        "TP (Accepted)": "#2ecc71",
                        "FP (Rejected)": "#e74c3c",
                        "Accepted w/ Changes": "#f39c12",
                        "Unreviewed": "#95a5a6",
                    },
                    title=f"Review Status by {drill}",
                    barmode="stack",
                )
                fig.update_layout(
                    xaxis_tickangle=-45,
                    legend_title_text="Status",
                    height=450,
                )
                st.plotly_chart(fig, use_container_width=True)

            with col2:
                # Summary metrics
                total = len(df_f)
                tp = (df_f["status"] == "TP (Accepted)").sum()
                fp = (df_f["status"] == "FP (Rejected)").sum()
                awc = (df_f["status"] == "Accepted w/ Changes").sum()
                unrev = (df_f["status"] == "Unreviewed").sum()

                st.metric("Total Findings", total)
                mcol1, mcol2 = st.columns(2)
                mcol1.metric("TP (Accepted)", tp)
                mcol2.metric("FP (Rejected)", fp)
                mcol1.metric("Accepted w/ Changes", awc)
                mcol2.metric("Unreviewed", unrev)

                if tp + fp + awc > 0:
                    precision = (tp + awc) / (tp + fp + awc)
                    st.metric("Precision (reviewed)", f"{precision:.1%}")
                else:
                    st.info("No reviewed findings yet — precision will appear once reviews are recorded.")

            # Detailed table
            with st.expander("Detailed Breakdown Table"):
                st.dataframe(
                    pivot.sort_values("Total", ascending=False),
                    use_container_width=True,
                    hide_index=True,
                )

            # Heatmap: Category vs Sub-Bucket
            if drill in ["Category", "Sub-Bucket"]:
                st.markdown("---")
                st.subheader("Category × Sub-Bucket Heatmap")
                heat_data = (
                    df_f.groupby(["category", "sub_bucket"]).size()
                    .reset_index(name="count")
                )
                heat_pivot = heat_data.pivot_table(
                    index="category", columns="sub_bucket", values="count", fill_value=0
                )
                fig_heat = px.imshow(
                    heat_pivot,
                    labels=dict(x="Sub-Bucket", y="Category", color="Findings"),
                    aspect="auto",
                    color_continuous_scale="RdYlGn_r",
                    title="Findings Density: Category × Sub-Bucket",
                )
                fig_heat.update_layout(height=500)
                st.plotly_chart(fig_heat, use_container_width=True)

    # ----- CONSISTENCY -----
    with q_con:
        st.subheader("Consistency — Raw Output vs Sequential Reasoner")
        st.markdown(
            "Compares findings between the **raw output** pass and the "
            "**sequential reasoner** pass to assess consistency."
        )

        if df_f.empty or df_s.empty:
            st.info("Need data from both raw_output and sequential_reasoner for comparison.")
        else:
            # Compare by artifact type: how many findings each approach found
            raw_counts = df_f.groupby("artifact_type").size().reset_index(name="raw_output")
            sr_counts = df_s.groupby("artifact_type").size().reset_index(name="sequential_reasoner")
            comparison = pd.merge(raw_counts, sr_counts, on="artifact_type", how="outer").fillna(0)
            comparison["raw_output"] = comparison["raw_output"].astype(int)
            comparison["sequential_reasoner"] = comparison["sequential_reasoner"].astype(int)
            comparison["diff"] = comparison["raw_output"] - comparison["sequential_reasoner"]
            comparison["agreement_pct"] = (
                comparison[["raw_output", "sequential_reasoner"]].min(axis=1)
                / comparison[["raw_output", "sequential_reasoner"]].max(axis=1)
                * 100
            ).fillna(0).round(1)

            col1, col2 = st.columns([2, 1])
            with col1:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    name="Raw Output",
                    x=comparison["artifact_type"],
                    y=comparison["raw_output"],
                    marker_color="#3498db",
                ))
                fig.add_trace(go.Bar(
                    name="Sequential Reasoner",
                    x=comparison["artifact_type"],
                    y=comparison["sequential_reasoner"],
                    marker_color="#e67e22",
                ))
                fig.update_layout(
                    barmode="group",
                    title="Findings Count: Raw vs Sequential Reasoner",
                    xaxis_tickangle=-45,
                    height=450,
                )
                st.plotly_chart(fig, use_container_width=True)

            with col2:
                overall_agreement = comparison["agreement_pct"].mean()
                st.metric("Avg Agreement", f"{overall_agreement:.1f}%")
                st.metric("Raw Output Findings", int(comparison["raw_output"].sum()))
                st.metric("Seq. Reasoner Findings", int(comparison["sequential_reasoner"].sum()))

            # Category-level consistency
            st.markdown("#### Category-Level Consistency")
            raw_cat = df_f.groupby(["artifact_type", "category"]).size().reset_index(name="raw_count")
            sr_cat = df_s.groupby(["artifact_type", "category"]).size().reset_index(name="sr_count")
            cat_comp = pd.merge(raw_cat, sr_cat, on=["artifact_type", "category"], how="outer").fillna(0)
            cat_comp["raw_count"] = cat_comp["raw_count"].astype(int)
            cat_comp["sr_count"] = cat_comp["sr_count"].astype(int)
            cat_comp["delta"] = cat_comp["raw_count"] - cat_comp["sr_count"]

            fig_cat = px.scatter(
                cat_comp,
                x="raw_count",
                y="sr_count",
                color="artifact_type",
                hover_data=["category"],
                title="Raw vs Reasoner Finding Counts by Category",
                labels={"raw_count": "Raw Output Count", "sr_count": "Sequential Reasoner Count"},
            )
            # Add diagonal line for perfect agreement
            max_val = max(cat_comp["raw_count"].max(), cat_comp["sr_count"].max()) + 1
            fig_cat.add_shape(
                type="line", x0=0, y0=0, x1=max_val, y1=max_val,
                line=dict(color="gray", dash="dash"),
            )
            fig_cat.update_layout(height=450)
            st.plotly_chart(fig_cat, use_container_width=True)

            with st.expander("Detailed Consistency Table"):
                st.dataframe(
                    comparison.sort_values("diff", key=abs, ascending=False),
                    use_container_width=True,
                    hide_index=True,
                )


# =========================================================================
# TAB 2 – COST
# =========================================================================
with tab_cost:
    st.subheader("Cost — Token Usage Analysis")

    if df_t.empty:
        st.info("No token data available for the current filters.")
    else:
        # --- Top-level metrics ---
        total_tokens = df_t["total_tokens"].sum()
        total_prompt = df_t["prompt_tokens"].sum()
        total_thoughts = df_t["thoughts_tokens"].sum()
        total_candidate = df_t["candidate_tokens"].sum()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Tokens", f"{total_tokens:,}")
        m2.metric("Prompt Tokens", f"{total_prompt:,}")
        m3.metric("Thinking Tokens", f"{total_thoughts:,}")
        m4.metric("Candidate Tokens", f"{total_candidate:,}")

        cost_doc, cost_art, cost_detail = st.tabs([
            "By Document", "By Artifact Type", "Detailed Breakdown"
        ])

        # --- By Document ---
        with cost_doc:
            doc_tokens = df_t.groupby("doc_label").agg(
                total=("total_tokens", "sum"),
                prompt=("prompt_tokens", "sum"),
                thoughts=("thoughts_tokens", "sum"),
                candidate=("candidate_tokens", "sum"),
            ).reset_index()

            fig = px.bar(
                doc_tokens.melt(id_vars="doc_label", value_vars=["prompt", "thoughts", "candidate"]),
                x="doc_label",
                y="value",
                color="variable",
                title="Token Usage by Document",
                labels={"value": "Tokens", "variable": "Token Type", "doc_label": "Document"},
                barmode="stack",
                color_discrete_map={
                    "prompt": "#3498db",
                    "thoughts": "#9b59b6",
                    "candidate": "#2ecc71",
                },
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)

        # --- By Artifact Type ---
        with cost_art:
            art_tokens = df_t.groupby("artifact_type").agg(
                total=("total_tokens", "sum"),
                prompt=("prompt_tokens", "sum"),
                thoughts=("thoughts_tokens", "sum"),
                candidate=("candidate_tokens", "sum"),
            ).reset_index().sort_values("total", ascending=True)

            fig = px.bar(
                art_tokens.melt(id_vars="artifact_type", value_vars=["prompt", "thoughts", "candidate"]),
                x="value",
                y="artifact_type",
                color="variable",
                title="Token Usage by Artifact Type",
                labels={"value": "Tokens", "variable": "Token Type", "artifact_type": "Artifact"},
                orientation="h",
                barmode="stack",
                color_discrete_map={
                    "prompt": "#3498db",
                    "thoughts": "#9b59b6",
                    "candidate": "#2ecc71",
                },
            )
            fig.update_layout(height=450)
            st.plotly_chart(fig, use_container_width=True)

            # Token efficiency: tokens per finding
            if not df_f.empty:
                findings_per_art = df_f.groupby("artifact_type").size().reset_index(name="findings")
                efficiency = pd.merge(
                    art_tokens[["artifact_type", "total"]],
                    findings_per_art,
                    on="artifact_type",
                    how="left",
                ).fillna(0)
                efficiency["tokens_per_finding"] = (
                    efficiency["total"] / efficiency["findings"].replace(0, float("nan"))
                ).fillna(0).astype(int)

                fig_eff = px.bar(
                    efficiency.sort_values("tokens_per_finding", ascending=True),
                    x="tokens_per_finding",
                    y="artifact_type",
                    orientation="h",
                    title="Token Efficiency: Tokens per Finding",
                    labels={"tokens_per_finding": "Tokens / Finding", "artifact_type": "Artifact"},
                    color="tokens_per_finding",
                    color_continuous_scale="RdYlGn_r",
                )
                fig_eff.update_layout(height=400)
                st.plotly_chart(fig_eff, use_container_width=True)

        # --- Detailed Breakdown ---
        with cost_detail:
            # Treemap: Document > Artifact > Token Type
            treemap_rows = []
            for _, row in df_t.iterrows():
                for ttype in ["prompt_tokens", "thoughts_tokens", "candidate_tokens"]:
                    treemap_rows.append({
                        "document": row["doc_label"],
                        "artifact": row["artifact_type"],
                        "token_type": ttype.replace("_tokens", ""),
                        "tokens": row[ttype],
                    })
            df_tree = pd.DataFrame(treemap_rows)
            df_tree = df_tree[df_tree["tokens"] > 0]

            if not df_tree.empty:
                fig_tree = px.treemap(
                    df_tree,
                    path=["document", "artifact", "token_type"],
                    values="tokens",
                    title="Token Distribution Treemap",
                    color="tokens",
                    color_continuous_scale="Blues",
                )
                fig_tree.update_layout(height=550)
                st.plotly_chart(fig_tree, use_container_width=True)

            # Cost by category/sub_bucket (via findings + token data)
            if not df_f.empty:
                st.markdown("#### Cost Attribution by Category")
                st.caption(
                    "Estimated by distributing artifact-level token cost "
                    "proportionally across findings in that artifact."
                )
                # For each artifact+doc combo, split tokens evenly across findings
                cost_rows = []
                for (uid, art), group in df_f.groupby(["uuid", "artifact_type"]):
                    token_match = df_t[
                        (df_t["uuid"] == uid) & (df_t["artifact_type"] == art)
                    ]
                    if token_match.empty:
                        continue
                    total = token_match["total_tokens"].iloc[0]
                    per_finding = total / len(group)
                    for _, finding in group.iterrows():
                        cost_rows.append({
                            "category": finding["category"],
                            "sub_bucket": finding["sub_bucket"],
                            "est_tokens": per_finding,
                        })

                if cost_rows:
                    df_cost_cat = pd.DataFrame(cost_rows)
                    cat_summary = (
                        df_cost_cat.groupby("category")["est_tokens"]
                        .sum().reset_index()
                        .sort_values("est_tokens", ascending=False)
                    )
                    fig_cost_cat = px.bar(
                        cat_summary,
                        x="category",
                        y="est_tokens",
                        title="Estimated Token Cost by Category",
                        labels={"est_tokens": "Estimated Tokens", "category": "Category"},
                        color="est_tokens",
                        color_continuous_scale="Reds",
                    )
                    fig_cost_cat.update_layout(xaxis_tickangle=-45, height=400)
                    st.plotly_chart(fig_cost_cat, use_container_width=True)

                    # Sub-bucket level
                    sub_summary = (
                        df_cost_cat.groupby(["category", "sub_bucket"])["est_tokens"]
                        .sum().reset_index()
                        .sort_values("est_tokens", ascending=False)
                    )
                    fig_sub = px.sunburst(
                        sub_summary,
                        path=["category", "sub_bucket"],
                        values="est_tokens",
                        title="Token Cost: Category → Sub-Bucket",
                        color="est_tokens",
                        color_continuous_scale="OrRd",
                    )
                    fig_sub.update_layout(height=550)
                    st.plotly_chart(fig_sub, use_container_width=True)


# =========================================================================
# TAB 3 – LATENCY
# =========================================================================
with tab_latency:
    st.subheader("Latency")
    st.info(
        "⏱️ Per-analysis latency data is not yet available in the current data model. "
        "Once timing fields (e.g., `start_time`, `end_time` per artifact) are added to the "
        "MongoDB documents, this tab will display:\n\n"
        "- **Per-artifact processing time**\n"
        "- **Document total processing time**\n"
        "- **Latency vs token count correlation**\n"
        "- **Latency percentiles (P50, P95, P99)**"
    )

    if not df_f.empty:
        st.markdown("#### Available Timing Data")
        st.markdown("Currently we can show document creation timestamps:")

        time_data = (
            df_f.groupby(["uuid", "doc_label", "created_at"])
            .size().reset_index(name="findings")
        )
        if not time_data.empty and time_data["created_at"].notna().any():
            st.dataframe(
                time_data[["doc_label", "created_at", "findings"]].rename(columns={
                    "doc_label": "Document",
                    "created_at": "Created At",
                    "findings": "Total Findings",
                }),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("---")
    st.markdown("#### Recommended Schema Addition")
    st.code("""
# Add to each document for full latency tracking:
{
    "misleading_latency": {
        "start_time": ISODate("..."),
        "end_time": ISODate("..."),
        "duration_ms": 4523
    },
    "performance_latency": { ... },
    # ... for each artifact type
    "total_processing_ms": 32150
}
    """, language="python")


# =========================================================================
# TAB 4 – CATEGORY DISTRIBUTION
# =========================================================================
with tab_categories:
    st.subheader("Category Distribution")
    st.markdown("Cross-cutting view of compliance finding categories across all axes.")

    if df_f.empty:
        st.info("No findings data available.")
    else:
        col1, col2 = st.columns(2)

        with col1:
            # Category counts
            cat_counts = df_f["category"].value_counts().reset_index()
            cat_counts.columns = ["category", "count"]
            fig_cat = px.pie(
                cat_counts,
                values="count",
                names="category",
                title="Findings by Category",
                hole=0.4,
            )
            fig_cat.update_traces(textposition="inside", textinfo="percent+value")
            fig_cat.update_layout(height=450)
            st.plotly_chart(fig_cat, use_container_width=True)

        with col2:
            # Sub-bucket counts (top 15)
            sub_counts = df_f["sub_bucket"].value_counts().head(15).reset_index()
            sub_counts.columns = ["sub_bucket", "count"]
            fig_sub = px.bar(
                sub_counts.sort_values("count", ascending=True),
                x="count",
                y="sub_bucket",
                orientation="h",
                title="Top 15 Sub-Buckets",
                color="count",
                color_continuous_scale="Viridis",
            )
            fig_sub.update_layout(height=450, showlegend=False)
            st.plotly_chart(fig_sub, use_container_width=True)

        # Category × Artifact Type heatmap
        st.markdown("#### Category × Artifact Type")
        cross = pd.crosstab(df_f["category"], df_f["artifact_type"])
        fig_cross = px.imshow(
            cross,
            labels=dict(x="Artifact Type", y="Category", color="Findings"),
            aspect="auto",
            color_continuous_scale="YlOrRd",
            title="Findings Heatmap: Category × Artifact Type",
        )
        fig_cross.update_layout(height=500)
        st.plotly_chart(fig_cross, use_container_width=True)

        # Category by Document
        st.markdown("#### Category by Document")
        doc_cat = pd.crosstab(df_f["doc_label"], df_f["category"])
        fig_dc = px.imshow(
            doc_cat,
            labels=dict(x="Category", y="Document", color="Findings"),
            aspect="auto",
            color_continuous_scale="Blues",
            title="Document × Category Heatmap",
        )
        fig_dc.update_layout(height=350)
        st.plotly_chart(fig_dc, use_container_width=True)

        # Detailed findings table
        with st.expander("Browse All Findings"):
            display_cols = [
                "doc_label", "artifact_type", "category", "sub_bucket",
                "page_number", "summary", "rule_citation", "status"
            ]
            df_display = df_f.copy()
            if "status" not in df_display.columns:
                df_display["status"] = df_display.apply(
                    lambda r: "TP" if r["accept"]
                    else ("FP" if r["reject"]
                          else ("w/ Changes" if r["accept_with_changes"]
                                else "Unreviewed")),
                    axis=1,
                )
            available_cols = [c for c in display_cols if c in df_display.columns]
            st.dataframe(
                df_display[available_cols],
                use_container_width=True,
                hide_index=True,
                height=500,
            )
