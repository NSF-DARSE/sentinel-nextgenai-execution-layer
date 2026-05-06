"""
Sentinel Unified Portal — Role-aware interface for customers and officers.
"""
import streamlit as st
import os
import requests
import time

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Sentinel", page_icon="🛡️", layout="wide")

# ── Simple Role Switcher ──────────────────────────────────────────────────────
# In a real app, this would be an Auth layer. For the demo, we use a sidebar.
st.sidebar.title("Sentinel Portal")
mode = st.sidebar.radio("Mode:", ["Customer", "Business"])
st.sidebar.markdown("---")


# ── Decision rendering helpers ────────────────────────────────────────────────
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}


def _risk_band(score: float | None, threshold: float) -> str:
    """Derive a band from the deterministic score. Not hardcoded anymore."""
    if score is None:
        return "Unknown"
    if score >= 0.90:
        return "Low Risk"
    if score >= threshold:
        return "Moderate Risk"
    return "Elevated Risk"


def _fetch_results(job_id: str) -> dict | None:
    try:
        r = requests.get(f"{API_URL}/jobs/{job_id}/results", timeout=10)
        return r.json() if r.ok else None
    except Exception:
        return None


def _render_score_breakdown(breakdown: list[dict], *, only_findings: bool = False):
    """Render the per-check breakdown. only_findings hides full-credit lines."""
    if not breakdown:
        st.caption("No detailed breakdown available.")
        return

    items = sorted(breakdown, key=lambda b: SEVERITY_ORDER.get(b.get("severity", "none"), 4))
    icon_for = {
        "critical": "🛑", "high": "⚠️", "medium": "🟡", "low": "🟢", "none": "✅",
    }
    for b in items:
        earned = b.get("points_earned", 0)
        possible = b.get("points_possible", 0)
        sev = b.get("severity", "none")
        if only_findings and earned == possible and sev == "none":
            continue
        icon = icon_for.get(sev, "•")
        code = b.get("code", "")
        detail = b.get("detail", "")
        st.markdown(f"{icon} **{code}** &nbsp; `{earned}/{possible} pts` — {detail}")


def _render_decision_card(job: dict, results: dict):
    """Customer-facing decision card. Score is deterministic, not LLM-derived."""
    score_data = results.get("score_breakdown") or {}
    extraction = results.get("extraction") or {}

    score = score_data.get("score")
    threshold = float(score_data.get("threshold") or 0.80)
    earned = score_data.get("total_earned", 0)
    possible = score_data.get("total_possible", 0)
    recommendation = score_data.get("recommendation", "—")
    breakdown = score_data.get("breakdown") or []

    # Top metrics row
    income = (extraction.get("income") or {}).get("monthly_net_estimated")
    band = _risk_band(score, threshold)

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Confidence Score",
        f"{int(round((score or 0) * 100))} / 100",
        help=f"Approval threshold is {int(threshold * 100)} / 100. "
             f"Earned {earned} of {possible} possible points.",
    )
    c2.metric("Risk Band", band)
    c3.metric(
        "Estimated Monthly Net",
        f"${float(income or 0):,.2f}" if income is not None else "Not extracted",
    )

    st.caption(
        f"📄 Document: **{job.get('filename', 'document')}** "
        f"&nbsp;·&nbsp; Recommendation: **{recommendation}**"
    )

    # Decision provenance — the part the user asked for
    with st.container(border=True):
        st.markdown(
            "**How this decision was made.** "
            "An AI model extracted structured fields from your document (income, "
            "balances, transaction signals). A **deterministic 100-point scorecard** "
            "then turned those fields into a recommendation using fixed rules and "
            "weights — every point added or deducted has a reason code below. "
            "The AI did not make the lending decision."
        )


def _render_customer_summary(data: dict):
    """Render the full customer summary based on the batch status response."""
    status = data.get("status")
    jobs = data.get("jobs") or []

    if status == "SUCCEEDED":
        st.success("✅ Application Approved")
    elif status == "NEEDS_REVIEW":
        st.warning(
            "⏳ Application Pending Review — a Best Egg specialist will look at "
            "the items below and follow up with you."
        )
    elif status == "FAILED":
        st.error("❌ We were unable to process one or more documents.")
    else:
        st.info(f"Current Status: **{status}**")

    if not jobs:
        return

    # Aggregate the worst recommendation across documents — that drives the message
    st.divider()
    st.subheader("📊 Application Summary")

    for idx, job in enumerate(jobs):
        if idx > 0:
            st.markdown("---")
        results = _fetch_results(job["job_id"])
        if not results:
            st.caption(
                f"Detailed analysis for **{job.get('filename', 'document')}** "
                f"is still being prepared."
            )
            continue

        _render_decision_card(job, results)

        score_data = results.get("score_breakdown") or {}
        breakdown = score_data.get("breakdown") or []
        flags = score_data.get("flags") or []
        recommendation = score_data.get("recommendation")

        # Reasons section — what's defensible to a regulator
        if recommendation == "NEEDS_REVIEW" or job.get("status") == "NEEDS_REVIEW":
            st.markdown("**Why your application is being reviewed**")
            findings = [
                b for b in breakdown
                if b.get("severity") in ("medium", "high", "critical")
                or (b.get("points_earned", 0) < b.get("points_possible", 0))
            ]
            if findings:
                _render_score_breakdown(findings, only_findings=False)
            else:
                st.caption("Routine review — no specific risk findings recorded.")
            if flags:
                st.caption("Reason codes: " + ", ".join(flags))
            st.info(
                "Under federal law (ECOA / Reg B), if this review results in an "
                "adverse decision you will receive a written notice listing the "
                "specific reason codes above."
            )
        else:
            with st.expander("See the full scorecard for this document"):
                _render_score_breakdown(breakdown, only_findings=False)

    st.info(
        "🔒 All personal data (Name, SSN, Account #) was redacted before any "
        "AI model saw the document. The redacted text is the only thing sent "
        "to the LLM."
    )


# ── Customer Portal Logic ─────────────────────────────────────────────────────
def render_customer():
    st.title("🛡️ Best Egg — Loan Portal")
    st.markdown("Upload your documents securely to complete your application.")

    uploaded_files = st.file_uploader(
        "Upload Documents (PDF)", type=["pdf"], accept_multiple_files=True
    )

    with st.form("upload_form"):
        submitted = st.form_submit_button("Submit Documents")

        if submitted:
            if not uploaded_files:
                st.warning("Please upload files first.")
            else:
                with st.spinner("Processing..."):
                    files = [
                        ("files", (f.name, f.getvalue(), "application/pdf"))
                        for f in uploaded_files
                    ]
                    try:
                        resp = requests.post(f"{API_URL}/batches/upload", files=files)
                        if resp.ok:
                            st.session_state.batch_id = resp.json().get("batch_id")
                            st.session_state.poll_count = 0
                            st.rerun()
                        else:
                            try:
                                err = resp.json().get("detail", "Upload failed.")
                            except Exception:
                                err = f"API error (Status {resp.status_code})"
                            st.error(f"Error: {err}")
                    except Exception as e:
                        st.error(f"Connection Error: {e}")

    if "batch_id" not in st.session_state:
        return

    batch_id = st.session_state.batch_id
    st.info(f"Tracking batch: `{batch_id}`")

    try:
        resp = requests.get(f"{API_URL}/batches/{batch_id}", timeout=10)
    except Exception as exc:
        st.error(f"Could not reach the API: {exc}")
        return

    if not resp.ok:
        st.error("Could not fetch status.")
        return

    data = resp.json()
    status = data.get("status")
    st.write(f"Current Status: **{status}**")

    if status == "RUNNING":
        # Cap polling so a stuck pipeline never spins the spinner forever.
        polls = st.session_state.get("poll_count", 0) + 1
        st.session_state.poll_count = polls
        if polls > 60:  # ~2 minutes at 2s intervals
            st.warning(
                "Processing is taking longer than expected. Your batch ID is saved "
                "above — check back in a few minutes or contact support if this "
                "persists."
            )
            return
        time.sleep(2)
        st.rerun()

    _render_customer_summary(data)


# ── Business Portal Logic ─────────────────────────────────────────────────────
def render_business():
    st.title("🔍 Business Dashboard")
    st.markdown(
        "Manual review queue. Each item shows the deterministic scorecard and "
        "reason codes that triggered review — use these as the basis for the "
        "adverse-action notice if you reject."
    )

    resp = requests.get(f"{API_URL}/jobs/review", timeout=10)
    if not resp.ok:
        st.error("Failed to load queue.")
        return

    queue = resp.json()
    if not queue:
        st.success("Review queue is empty.")
        return

    for item in queue:
        score = item.get("confidence_score")
        score_label = f"{int(round(score * 100))}/100" if score is not None else "n/a"
        header = f"📄 {item['filename']} — Score {score_label}"
        with st.expander(header):
            results = _fetch_results(item["job_id"])
            if results:
                score_data = results.get("score_breakdown") or {}
                flags = score_data.get("flags") or []
                breakdown = score_data.get("breakdown") or []

                if flags:
                    st.markdown("**Reason codes (for adverse-action notice):**")
                    st.code(", ".join(flags), language=None)

                st.markdown("**Deterministic scorecard**")
                _render_score_breakdown(breakdown, only_findings=False)

                auth = results.get("authenticity_report") or {}
                if auth.get("flags"):
                    st.markdown("**Authenticity flags:**")
                    for f in auth["flags"]:
                        st.markdown(f"- {f}")
            else:
                st.caption("Score breakdown not yet available for this job.")

            st.markdown(
                f"**Pipeline note:** {item.get('error_message') or 'No notes recorded.'}"
            )
            st.caption(f"PII types redacted: {item.get('pii_types_found') or 'none'}")

            c1, c2 = st.columns(2)
            if c1.button("Approve", key=f"app_{item['job_id']}"):
                requests.post(
                    f"{API_URL}/jobs/{item['job_id']}/review",
                    json={"decision": "approved"},
                )
                st.rerun()
            if c2.button("Reject", key=f"rej_{item['job_id']}"):
                requests.post(
                    f"{API_URL}/jobs/{item['job_id']}/review",
                    json={
                        "decision": "rejected",
                        "notes": "; ".join(
                            (results.get("score_breakdown") or {}).get("flags") or []
                        ) if results else "",
                    },
                )
                st.rerun()


# ── Routing ──────────────────────────────────────────────────────────────────
if mode == "Customer":
    render_customer()
else:
    render_business()
