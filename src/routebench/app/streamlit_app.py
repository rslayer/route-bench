"""Streamlit UI for RouteBench."""

from __future__ import annotations

import json
import os
import time

import httpx
import streamlit as st

API_BASE = os.environ.get("ROUTEBENCH_API_URL", "http://localhost:8000")

st.set_page_config(page_title="RouteBench", page_icon="🚛", layout="wide")
st.title("🚛 RouteBench — Route Analysis")

# Configuration sidebar
with st.sidebar:
    st.header("Configuration")
    include_benchmark = st.checkbox("Include benchmark comparison", value=True)
    include_pdf = st.checkbox("Generate PDF report", value=False)
    sequencing_threshold = st.slider(
        "Sequencing threshold",
        min_value=1.0,
        max_value=2.0,
        value=1.3,
        step=0.05,
    )

# File upload
uploaded_file = st.file_uploader("Upload your route CSV", type=["csv"])

if uploaded_file is not None and st.button("Analyze Routes", type="primary"):
    config = {
        "include_benchmark": include_benchmark,
        "include_pdf": include_pdf,
        "sequencing_threshold": sequencing_threshold,
    }

    # Upload to API
    with st.spinner("Uploading..."):
        try:
            resp = httpx.post(
                f"{API_BASE}/sessions",
                files={"file": (uploaded_file.name, uploaded_file.getvalue(), "text/csv")},
                data={"config": json.dumps(config)},
                timeout=30.0,
            )
        except httpx.ConnectError:
            st.error(
                "Could not connect to the API server. Make sure it's running on localhost:8000."
            )
            st.stop()

    if resp.status_code == 422:
        detail = resp.json().get("detail", {})
        if isinstance(detail, dict) and "validation_errors" in detail:
            st.error("CSV validation failed:")
            for err in detail["validation_errors"]:
                st.warning(f"**{err['code']}**: {err['message']}")
        else:
            st.error(f"Validation error: {detail}")
        st.stop()
    elif resp.status_code == 429:
        st.error("Server is busy. Please try again in a few minutes.")
        st.stop()
    elif resp.status_code == 503:
        st.error("Daily budget exceeded. Service resumes at UTC midnight.")
        st.stop()
    elif resp.status_code != 202:
        st.error(f"Unexpected error: {resp.status_code} — {resp.text}")
        st.stop()

    session_data = resp.json()
    session_id = session_data["session_id"]

    # Poll for progress
    progress_bar = st.progress(0, text="Queued for processing...")
    status_placeholder = st.empty()

    while True:
        try:
            status_resp = httpx.get(
                f"{API_BASE}/sessions/{session_id}",
                timeout=10.0,
            )
            if status_resp.status_code != 200:
                st.error(f"Failed to get status: {status_resp.status_code}")
                break

            status = status_resp.json()
            state = status["state"]
            pct = status["progress_pct"]
            detail = status["stage_detail"]

            progress_bar.progress(pct / 100, text=detail)

            if state == "succeeded":
                st.success("Report generated successfully!")

                # Show download links
                col1, col2 = st.columns(2)
                with col1:
                    st.link_button(
                        "Download HTML Report",
                        f"{API_BASE}/sessions/{session_id}/report.html",
                    )
                with col2:
                    if status.get("artifacts", {}).get("report_pdf"):
                        st.link_button(
                            "Download PDF Report",
                            f"{API_BASE}/sessions/{session_id}/report.pdf",
                        )

                # Show cost if available
                cost = status.get("cost")
                if cost:
                    with st.expander("Session Cost"):
                        st.metric("Input Tokens", f"{cost['input_tokens']:,}")
                        st.metric("Output Tokens", f"{cost['output_tokens']:,}")
                        st.metric("LLM Cost", f"${cost['llm_cost_usd']:.4f}")

                # Render report inline
                try:
                    report_resp = httpx.get(
                        f"{API_BASE}/sessions/{session_id}/report.html",
                        follow_redirects=True,
                        timeout=30.0,
                    )
                    if report_resp.status_code == 200:
                        st.components.v1.html(
                            report_resp.text,
                            height=800,
                            scrolling=True,
                        )
                except Exception:
                    pass  # Inline rendering is best-effort

                break

            elif state == "failed":
                error = status.get("error", {})
                st.error(f"Analysis failed: {error.get('message', detail)}")
                break

        except httpx.ConnectError:
            st.error("Lost connection to API server.")
            break

        time.sleep(1.0)
