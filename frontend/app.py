"""
Sentinel Unified Portal — Role-aware interface for customers and officers.
"""
import streamlit as st
import os
import requests

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Sentinel", page_icon="🛡️", layout="wide")

# ── Simple Role Switcher ──────────────────────────────────────────────────────
# In a real app, this would be an Auth layer. For the demo, we use a sidebar.
st.sidebar.title("Sentinel Portal")
mode = st.sidebar.radio("Mode:", ["Customer", "Officer"])
st.sidebar.markdown("---")

# ── Customer Portal Logic ─────────────────────────────────────────────────────
def render_customer():
    st.title("🛡️ Best Egg — Loan Portal")
    st.markdown("Upload your documents securely to complete your application.")
    
    uploaded_files = st.file_uploader("Upload Documents (PDF)", type=["pdf"], accept_multiple_files=True)
    if uploaded_files and st.button("Submit"):
        with st.spinner("Processing..."):
            files = [("files", (f.name, f.getvalue(), "application/pdf")) for f in uploaded_files]
            resp = requests.post(f"{API_URL}/batches/upload", files=files)
            if resp.ok:
                st.success("Uploaded! Your application is being reviewed.")
                st.rerun()
            else:
                # Handle our new descriptive error codes
                err = resp.json().get("detail", "Upload failed.")
                if "GUARDRAIL_REJECTED" in err:
                    st.error("We couldn't read your documents. Please ensure you've uploaded valid bank statements or paystubs.")
                else:
                    st.error(f"Error: {err}")

# ── Officer Portal Logic ──────────────────────────────────────────────────────
def render_officer():
    st.title("🔍 Loan Officer Dashboard")
    st.markdown("Risk analysis and manual review queue.")
    
    resp = requests.get(f"{API_URL}/jobs/review")
    if resp.ok:
        queue = resp.json()
        for item in queue:
            with st.expander(f"📄 {item['filename']} — Risk Score: {item.get('confidence_score', 'N/A')}"):
                st.write(f"**System Justification:** {item.get('error_message', 'No notes provided.')}")
                st.write(f"**Flags:** {item.get('pii_types_found', 'None')}")
                if st.button("Approve", key=f"app_{item['job_id']}"):
                    requests.post(f"{API_URL}/jobs/{item['job_id']}/review", json={"decision": "approved"})
                    st.rerun()
    else:
        st.error("Failed to load queue.")

# ── Routing ──────────────────────────────────────────────────────────────────
if mode == "Customer":
    render_customer()
else:
    render_officer()
