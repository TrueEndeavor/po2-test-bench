#!/bin/bash
# Launcher for PO2 Test Bench
cd "$(dirname "$0")"
streamlit run main.py --server.headless true --server.port 8501
