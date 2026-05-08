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


def _fmt_pct(v):
    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "—"


def _fmt_score(v):
    return f"{v:.2f}" if isinstance(v, (int, float)) else "—"


# ── Metrics strip ─────────────────────────────────────────────────────────────
metrics_resp = api_get("/metrics/dashboard")
if metrics_resp.ok:
    m = metrics_resp.json()
    totals = m["totals"]
    rates = m["rates"]
    statuses = m["status_counts"]

    row1 = st.columns(4)
    row1[0].metric("Documents (24h)", totals["processed_24h"])
    row1[1].metric("All-time processed", totals["all_jobs"])
    row1[2].metric("PII entities redacted", totals["pii_entities_redacted"])
    row1[3].metric("Pending review", statuses.get("NEEDS_REVIEW", 0))

    row2 = st.columns(4)
    row2[0].metric("Auto-approval rate", _fmt_pct(rates["auto_approval_rate"]))
    row2[1].metric("Auth pass rate", _fmt_pct(rates["auth_pass_rate"]))
    row2[2].metric("Avg confidence", _fmt_score(rates["avg_confidence"]))
    row2[3].metric("Avg auth confidence", _fmt_score(rates["avg_auth_confidence"]))

    chart_data = {
        "Status": ["Succeeded", "Needs review", "Failed", "Running", "Queued"],
        "Count": [
            statuses.get("SUCCEEDED", 0),
            statuses.get("NEEDS_REVIEW", 0),
            statuses.get("FAILED", 0),
            statuses.get("RUNNING", 0),
            statuses.get("QUEUED", 0),
        ],
    }
    st.bar_chart(chart_data, x="Status", y="Count", height=220)
else:
    st.warning("Could not load dashboard metrics.")

st.divider()

# ── Review queue ──────────────────────────────────────────────────────────────
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
        score = item.get("confidence_score")
        score_label = f"Score: {score:.2f}" if isinstance(score, (int, float)) else "Score: N/A"
        reason = item.get("error_message") or "Below confidence threshold"

        with st.expander(f"📄 {item['filename']} — {score_label}"):
            st.markdown(f"**Reason flagged:** {reason}")
            st.caption(f"Job ID: `{job_id}`")

            cols = st.columns(2)
            if cols[0].button("Approve", key=f"app_{job_id}"):
                api_post(f"/jobs/{job_id}/review", json={"decision": "approved"})
                st.rerun()
            if cols[1].button("Reject", key=f"rej_{job_id}"):
                api_post(f"/jobs/{job_id}/review", json={"decision": "rejected"})
                st.rerun()
else:
    st.error("Could not load queue.")
