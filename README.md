# PO2 Test Bench

A Ground Truth testing and validation platform for compliance document analysis with weighted scoring and cute run names! 🎯

📊 **[Executive Summary](EXECUTIVE_SUMMARY.md)** - Quick feature overview for stakeholders

## Features

- 🎯 **Weighted GT Scoring**: Exact match (1.0) + Partial match (0.5) + Theme suppression
- 📊 **Consolidated Metrics**: Real-time aggregated metrics across all test cases
- 🔄 **Run Tracking**: Auto-generated cute names (e.g., "Bubbles-2026-02-10") for A/B testing
- 📈 **Detailed Comparison**: Per-TC breakdowns, missing findings (FN), and GT validation
- 🚀 **Live Updates**: Metrics update automatically as you run more test cases

## Deployment on Streamlit Cloud

### 1. Push to GitHub
```bash
git add .
git commit -m "Deploy PO2 Test Bench"
git push origin main
```

### 2. Deploy on Streamlit Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io/)
2. Click "New app"
3. Select repository: `TrueEndeavor/po2-test-bench`
4. Set main file: `main.py`
5. Click "Advanced settings" and add secrets:

```toml
# MongoDB Connection
MONGODB_URI = "mongodb+srv://your-connection-string"

# API Authentication (if needed)
API_AUTH_TOKEN = "Bearer your-token-here"
```

6. Click "Deploy"!

### 3. Configure Secrets

In Streamlit Cloud dashboard:
- Go to App settings → Secrets
- Add the same environment variables from your `.env` file

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run main.py --server.port 8501
```

## Ground Truth CSV Format

The `ground_truth.csv` should have these columns:
- TC Id
- Document
- Page Number
- Non compliant (text to flag)
- Compliant (corrected text)
- Category
- Sub Bucket
- Reasoning
- Rule citation
- Remarks

## Scoring System

- **Exact Match (TP)**: 1.0 point - Exact sentence match
- **Partial Match**: 0.5 points - Same theme + page but different wording
- **False Positive**: 0 points - Valid theme but wrong page/context
- **Suppressed**: Findings from themes not in GT are ignored

## Run Names

Each test session gets a cute random name:
- Bubbles, Sprinkles, Cupcake, Muffin, Luna, Nova, etc.
- Format: `Bubbles-2026-02-10-13-45-23`
- Perfect for tracking A/B test comparisons!

## Tech Stack

- Streamlit
- MongoDB (via pymongo)
- Pandas & Plotly
- Python 3.8+
