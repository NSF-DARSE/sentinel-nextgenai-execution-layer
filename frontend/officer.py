"""
Sentinel Loan Officer Dashboard — Internal document review and risk management.
"""
import os
import requests
import streamlit as st
import time

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Sentinel Officer Dashboard",
    page_icon="🔍",
    layout="wide",
)

st.title("🔍 Loan Officer Dashboard")
st.markdown("Review flagged loan applications and PII-redacted documents.")

# ── API helpers ───────────────────────────────────────────────────────────────
def api_get(path: str):
    return requests.get(f"{API_URL}{path}", timeout=10)

def api_post(path: str, **kwargs):
    return requests.post(f"{API_URL}{path}", timeout=30, **kwargs)

# ── UI ────────────────────────────────────────────────────────────────────────
st.header("Pending Human Reviews")

if st.button("Refresh Queue"):
    st.rerun()

resp = api_get("/jobs/review")
if resp.ok:
    queue = resp.json()
    if not queue:
        st.success("No documents pending review.")
    for item in queue:
        job_id = str(item["job_id"])
        with st.expander(f"📄 {item['filename']} — Score: {item.get('confidence_score', 'N/A')}"):
            st.write(f"Job ID: {job_id}")
            
            if st.button("Approve", key=f"app_{job_id}"):
                api_post(f"/jobs/{job_id}/review", json={"decision": "approved"})
                st.rerun()
            if st.button("Reject", key=f"rej_{job_id}"):
                api_post(f"/jobs/{job_id}/review", json={"decision": "rejected"})
                st.rerun()
else:
    st.error("Could not load queue.")
