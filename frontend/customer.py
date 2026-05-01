"""
Sentinel Customer Portal — Financial document submission and tracking.
"""
import os
import requests
import streamlit as st
import time

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Sentinel Customer Portal",
    page_icon="📄",
    layout="wide",
)

st.title("🛡️ Best Egg — Document Submission")
st.markdown("Securely upload your financial documents to complete your loan application.")

# ── API helpers ───────────────────────────────────────────────────────────────
def api_post(path: str, **kwargs):
    return requests.post(f"{API_URL}{path}", timeout=30, **kwargs)

def api_get(path: str):
    return requests.get(f"{API_URL}{path}", timeout=10)

# ── UI ────────────────────────────────────────────────────────────────────────

uploaded_files = st.file_uploader(
    "Choose PDF files (Bank Statements, Paystubs, W-2s)",
    type=["pdf"],
    accept_multiple_files=True
)

if uploaded_files:
    if st.button("Submit Documents for Review", type="primary"):
        with st.spinner("Analyzing documents..."):
            files_payload = [
                ("files", (f.name, f.getvalue(), "application/pdf"))
                for f in uploaded_files
            ]
            resp = api_post("/batches/upload", files=files_payload)
            if resp.ok:
                data = resp.json()
                st.session_state.batch_id = data["batch_id"]
                st.rerun()
            else:
                st.error("Upload failed.")

batch_id = st.session_state.get("batch_id")
if batch_id:
    st.info(f"Your application is being processed. Batch ID: `{batch_id}`")
    resp = api_get(f"/batches/{batch_id}")
    if resp.ok:
        data = resp.json()
        st.write(f"Status: **{data['status']}**")
        if data['status'] == 'RUNNING':
            time.sleep(2)
            st.rerun()
        elif data['status'] == 'SUCCEEDED':
            st.success("Your documents have been processed and your application is approved!")
        elif data['status'] == 'FAILED':
            st.error("There was an issue processing your documents. Please contact support.")
