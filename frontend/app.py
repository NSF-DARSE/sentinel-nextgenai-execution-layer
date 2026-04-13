"""
Sentinel Frontend — Streamlit UI for the document intelligence pipeline.

Two tabs:
  Upload & Track  — upload a PDF, watch the pipeline run, see full results
                    with deterministic score breakdown and redacted diff preview
  Review Queue    — see all NEEDS_REVIEW jobs, read the reason codes, approve or reject
"""
import json
import re
import time
from collections import Counter

import requests
import streamlit as st

API_URL = st.secrets.get("API_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Sentinel",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Constants ─────────────────────────────────────────────────────────────────

STATUS_ICON = {
    "QUEUED":       "⏳",
    "RUNNING":      "🔄",
    "SUCCEEDED":    "✅",
    "NEEDS_REVIEW": "⚠️",
    "FAILED":       "❌",
}

TERMINAL = {"SUCCEEDED", "NEEDS_REVIEW", "FAILED"}

# Color map for PII placeholder types in the redaction preview
ENTITY_COLORS = {
    "PERSON":           ("#c62828", "#ffebee"),
    "US_SSN":           ("#b71c1c", "#ffcdd2"),
    "CREDIT_CARD":      ("#b71c1c", "#ffcdd2"),
    "ACCOUNT_NUMBER":   ("#e65100", "#fff3e0"),
    "US_BANK_NUMBER":   ("#e65100", "#fff3e0"),
    "PHONE_NUMBER":     ("#1565c0", "#e3f2fd"),
    "EMAIL_ADDRESS":    ("#1565c0", "#e3f2fd"),
    "LOCATION":         ("#2e7d32", "#e8f5e9"),
    "DATE_TIME":        ("#4a148c", "#f3e5f5"),
    "NRP":              ("#4a148c", "#f3e5f5"),
}
DEFAULT_ENTITY_COLOR = ("#37474f", "#eceff1")

SEVERITY_COLOR = {
    "none":     "🟢",
    "low":      "🔵",
    "medium":   "🟡",
    "high":     "🔴",
    "critical": "🚨",
}

CATEGORY_LABEL = {
    "completeness": "Layer 1 — Document Completeness",
    "integrity":    "Layer 2 — Integrity & Authentication",
    "risk":         "Layer 3 — Risk Signals",
    "coverage":     "Layer 4 — LLM Field Coverage",
}

# ── API helpers ───────────────────────────────────────────────────────────────

def api_get(path: str):
    return requests.get(f"{API_URL}{path}", timeout=10)

def api_post(path: str, **kwargs):
    return requests.post(f"{API_URL}{path}", timeout=30, **kwargs)

def check_api() -> bool:
    try:
        return api_get("/health").ok
    except requests.exceptions.ConnectionError:
        return False

# ── Markdown safety helper ────────────────────────────────────────────────────

def _md(text: str) -> str:
    """
    Escape characters that Streamlit's markdown renderer treats specially.
    Dollar signs are the primary culprit — Streamlit >= 1.35 interprets
    $...$ as LaTeX math, so a note like "balance ($3,500) does not match ($3,501)"
    gets sent to the LaTeX engine and renders as garbled character-per-line output.
    """
    if not text:
        return text
    return text.replace("$", r"\$")


# ── Redaction preview renderer ────────────────────────────────────────────────

def colorize_redacted(text: str) -> str:
    """
    Replace [ENTITY_TYPE] placeholders with color-coded HTML spans.
    Each entity type gets a unique color so the reviewer can see at a glance
    what categories of PII were present and removed.
    """
    def replace(m):
        entity = m.group(1)
        fg, bg = ENTITY_COLORS.get(entity, DEFAULT_ENTITY_COLOR)
        return (
            f'<span style="background:{bg};color:{fg};border:1px solid {fg}33;'
            f'padding:1px 5px;border-radius:3px;font-weight:600;font-size:0.85em">'
            f'[{entity}]</span>'
        )
    # Escape HTML in the original text first, then insert spans
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r'\[([A-Z_]+)\]', replace, safe)

# ── Score breakdown renderer ──────────────────────────────────────────────────

def render_score_breakdown(score_data: dict) -> None:
    score       = score_data.get("score", 0)
    earned      = score_data.get("total_earned", 0)
    possible    = score_data.get("total_possible", 100)
    threshold   = score_data.get("threshold", 0.80)
    flags       = score_data.get("flags", [])
    breakdown   = score_data.get("breakdown", [])
    rec         = score_data.get("recommendation", "NEEDS_REVIEW")

    # Score header
    pct = int(score * 100)
    rec_color = "green" if rec == "PASS" else "orange"
    st.markdown(
        f"### Confidence Score: `{score:.2f}` &nbsp; ({earned}/{possible} pts)"
    )
    col_score, col_rec, col_thresh = st.columns(3)
    with col_score:
        st.progress(score, text=f"{pct}%")
    with col_rec:
        icon = "✅" if rec == "PASS" else "⚠️"
        st.markdown(f"**Recommendation:** {icon} `{rec}`")
    with col_thresh:
        st.caption(f"Threshold: {threshold:.2f} | Flags: {len(flags)}")

    if flags:
        st.markdown(
            " ".join(
                f'<span style="background:#fff3e0;color:#e65100;border:1px solid #ffcc02;'
                f'padding:2px 6px;border-radius:4px;font-size:0.8em">{f}</span>'
                for f in flags
            ),
            unsafe_allow_html=True,
        )

    # Group by category
    by_category: dict[str, list] = {}
    for item in breakdown:
        by_category.setdefault(item["category"], []).append(item)

    for cat_key, label in CATEGORY_LABEL.items():
        items = by_category.get(cat_key, [])
        if not items:
            continue
        cat_earned   = sum(i["points_earned"] for i in items)
        cat_possible = sum(i["points_possible"] for i in items)
        st.markdown(f"**{label}** &nbsp; `{cat_earned}/{cat_possible} pts`")
        for item in items:
            sev   = item.get("severity", "none")
            icon  = SEVERITY_COLOR.get(sev, "⚪")
            pts   = item["points_earned"]
            total = item["points_possible"]
            code  = item["code"]
            detail = item["detail"]
            delta  = f"+{pts}" if pts == total else f"+{pts}/{total}"
            st.markdown(
                f'&nbsp;&nbsp;&nbsp;{icon} `{code}` &nbsp; **{delta} pts** — {_md(detail)}'
            )

# ── Results renderer ──────────────────────────────────────────────────────────

def render_results(results: dict, job: dict) -> None:
    extraction = results.get("extraction", {})
    score_data = results.get("score_breakdown")
    redaction_report = results.get("redaction_report", [])

    if not extraction:
        st.warning("Extraction results not yet available.")
        return

    doc_type = extraction.get("document_type", "unknown")
    income   = extraction.get("income") or {}
    account  = extraction.get("account_summary") or {}
    risk     = extraction.get("risk_flags") or {}
    expenses = extraction.get("recurring_expenses") or []

    # ── Income + Account ──────────────────────────────────────────────────────
    col_inc, col_acc = st.columns(2)

    with col_inc:
        st.subheader("💰 Income")
        monthly = income.get("monthly_net_estimated")
        annual  = income.get("annual_gross")
        st.metric("Monthly Net (estimated)", f"${monthly:,.2f}" if monthly else "—")
        if annual:
            st.metric("Annual Gross", f"${annual:,.2f}")
        st.metric("Pay Frequency", income.get("frequency", "—").title())
        sources = income.get("sources") or []
        if sources:
            st.caption("Sources: " + ", ".join(sources))

    with col_acc:
        st.subheader("🏦 Account Summary")
        opening = account.get("opening_balance")
        closing = account.get("closing_balance")
        avg     = account.get("average_daily_balance_estimated")
        if opening is None and closing is None:
            st.caption(f"Not applicable for `{doc_type}`")
        else:
            st.metric("Opening Balance", f"${opening:,.2f}" if opening is not None else "—")
            delta = f"{closing - opening:+,.2f}" if (opening is not None and closing is not None) else None
            st.metric("Closing Balance", f"${closing:,.2f}" if closing is not None else "—", delta=delta)
            st.metric("Avg Daily Balance", f"${avg:,.2f}" if avg is not None else "—")

    st.divider()

    # ── Risk flags ────────────────────────────────────────────────────────────
    st.subheader("🚦 Risk Flags")
    flag_cols = st.columns(3)
    flag_items = [
        ("Overdrafts",             risk.get("overdraft_occurrences", 0),  risk.get("overdraft_occurrences", 0) > 0),
        ("NSF Fees",               risk.get("nsf_fee_occurrences", 0),    risk.get("nsf_fee_occurrences", 0) > 0),
        ("Large Cash Withdrawals", risk.get("large_cash_withdrawals"),     risk.get("large_cash_withdrawals")),
        ("Gambling Transactions",  risk.get("gambling_transactions"),      risk.get("gambling_transactions")),
        ("Irregular Deposits",     risk.get("irregular_large_deposits"),   risk.get("irregular_large_deposits")),
        ("Integrity Issue",        risk.get("document_integrity_flag"),    risk.get("document_integrity_flag")),
    ]
    for i, (label, value, bad) in enumerate(flag_items):
        with flag_cols[i % 3]:
            icon = "⚠️" if bad else "✅"
            disp = str(value) if isinstance(value, int) else ("Yes" if value else "No")
            st.metric(f"{icon} {label}", disp)

    notes = risk.get("notes")
    if notes:
        st.info(f"**LLM analysis note:** {_md(notes)}")

    # ── Score breakdown ───────────────────────────────────────────────────────
    if score_data:
        st.divider()
        st.subheader("📊 Score Breakdown")
        st.caption("Every point deduction has a reason code. This is what you show stakeholders.")
        render_score_breakdown(score_data)

    # ── Recurring expenses ────────────────────────────────────────────────────
    if expenses:
        st.divider()
        st.subheader("🔁 Recurring Expenses")
        for exp in expenses:
            amt = exp.get("average_monthly_amount")
            amt_str = f"${amt:,.2f}/mo" if amt else ""
            st.markdown(f"- **{exp.get('category', '?')}** — {exp.get('frequency', '').title()} {amt_str}")

    # ── Redaction summary ─────────────────────────────────────────────────────
    if redaction_report:
        st.divider()
        st.subheader("🔒 Redaction Summary")
        counts = Counter(e.get("entity_type") for e in redaction_report)
        r_cols = st.columns(min(len(counts), 5))
        for i, (etype, count) in enumerate(sorted(counts.items())):
            with r_cols[i % len(r_cols)]:
                fg, bg = ENTITY_COLORS.get(etype, DEFAULT_ENTITY_COLOR)
                st.markdown(
                    f'<div style="background:{bg};color:{fg};border:1px solid {fg}44;'
                    f'padding:8px;border-radius:6px;text-align:center">'
                    f'<strong>{count}</strong><br><small>{etype}</small></div>',
                    unsafe_allow_html=True,
                )

# ── Tab 1: Upload & Track ─────────────────────────────────────────────────────

def render_upload_tab():
    st.header("Upload Document")
    st.caption(
        "Upload a PDF bank statement, paystub, W-2, or tax return. "
        "The pipeline will parse it, redact all PII, run Gemini extraction, "
        "and compute a deterministic confidence score with reason codes."
    )

    uploaded = st.file_uploader("Choose a PDF file", type=["pdf"])

    if uploaded:
        if st.button("Upload & Process", type="primary"):
            with st.spinner("Uploading to pipeline..."):
                try:
                    resp = api_post(
                        "/documents/upload",
                        files={"file": (uploaded.name, uploaded.getvalue(), "application/pdf")},
                    )
                    if resp.ok:
                        data = resp.json()
                        st.session_state.job_id = data["job_id"]
                        st.rerun()
                    else:
                        st.error(f"Upload failed ({resp.status_code}): {resp.text}")
                except requests.exceptions.ConnectionError:
                    st.error(
                        "Cannot connect to API at `http://localhost:8000`. "
                        "Run `docker-compose up` first."
                    )

    job_id = st.session_state.get("job_id")
    if not job_id:
        return

    st.divider()
    st.markdown(f"**Job ID:** `{job_id}`")

    try:
        job_resp = api_get(f"/jobs/{job_id}")
    except requests.exceptions.ConnectionError:
        st.error("Lost connection to API.")
        return

    if not job_resp.ok:
        st.error(f"Could not fetch job status ({job_resp.status_code})")
        return

    job    = job_resp.json()
    status = job["status"]
    icon   = STATUS_ICON.get(status, "?")

    st.markdown(f"**Status:** {icon} `{status}`")

    if status not in TERMINAL:
        st.progress({"QUEUED": 0.05, "RUNNING": 0.5}.get(status, 0.05))
        st.caption("Auto-refreshing every 2 seconds...")
        time.sleep(2)
        st.rerun()

    # One-shot settle: first time we land on a terminal status, give MinIO a moment
    # to finish writing artifacts before we try fetching them.
    settle_key = f"settled_{job_id}"
    if not st.session_state.get(settle_key):
        st.session_state[settle_key] = True
        time.sleep(1.5)
        st.rerun()

    if status == "FAILED":
        error = job.get("error_message") or "Unknown error"
        stage = "unknown stage"
        if error.startswith("["):
            stage = error.split("]")[0].strip("[")
            error = error.split("]", 1)[-1].strip()
        st.error(f"**Pipeline failed at stage: `{stage}`**\n\n{error}")
        if st.button("Clear and upload another"):
            st.session_state.pop("job_id", None)
            st.rerun()
        return

    if status == "NEEDS_REVIEW":
        st.warning(
            "This document was routed to human review. "
            "Check the score breakdown below to see exactly which checks failed. "
            "A reviewer must approve or reject it in the **Review Queue** tab."
        )
    elif status == "SUCCEEDED":
        st.success("Pipeline completed. All checks passed.")

    # ── Redaction preview ─────────────────────────────────────────────────────
    with st.expander("🔒 Redacted Document Preview", expanded=False):
        st.caption(
            "This is what Gemini saw — all PII replaced with typed placeholders. "
            "Color = entity type. The raw PDF was deleted from storage after processing."
        )
        try:
            prev_resp = api_get(f"/jobs/{job_id}/redacted-preview")
            if prev_resp.ok:
                prev = prev_resp.json()
                redacted_text = prev.get("redacted_text", "")

                # Legend
                seen_types = set(re.findall(r'\[([A-Z_]+)\]', redacted_text))
                if seen_types:
                    legend_parts = []
                    for etype in sorted(seen_types):
                        fg, bg = ENTITY_COLORS.get(etype, DEFAULT_ENTITY_COLOR)
                        legend_parts.append(
                            f'<span style="background:{bg};color:{fg};border:1px solid {fg}44;'
                            f'padding:1px 6px;border-radius:3px;font-size:0.8em">[{etype}]</span>'
                        )
                    st.markdown(" ".join(legend_parts), unsafe_allow_html=True)

                st.markdown(
                    f'<div style="background:#fafafa;border:1px solid #e0e0e0;padding:16px;'
                    f'border-radius:8px;font-family:monospace;font-size:0.82em;'
                    f'max-height:420px;overflow-y:auto;white-space:pre-wrap;line-height:1.7">'
                    f'{colorize_redacted(redacted_text)}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.info("Redacted preview not yet available.")
        except Exception as e:
            st.warning(f"Could not load preview: {e}")

    # ── Full results ──────────────────────────────────────────────────────────
    try:
        res_resp = api_get(f"/jobs/{job_id}/results")
        if res_resp.ok:
            render_results(res_resp.json(), job)
        elif res_resp.status_code == 404:
            col_msg, col_btn = st.columns([4, 1])
            with col_msg:
                st.info("Results are still being finalized...")
            with col_btn:
                if st.button("🔄 Refresh", key="refresh_results"):
                    # clear settle flag so timing doesn't re-trigger
                    st.session_state.pop(settle_key, None)
                    st.rerun()
        else:
            st.warning(f"Could not load results ({res_resp.status_code}).")
    except Exception as e:
        st.error(f"Could not load results: {e}")

    st.divider()
    if st.button("Upload another document"):
        st.session_state.pop("job_id", None)
        st.rerun()


# ── Tab 2: Review Queue ───────────────────────────────────────────────────────

def render_review_tab():
    st.header("Review Queue")
    st.caption(
        "Documents flagged for human review. "
        "Each card shows the exact reason codes — not just a score — "
        "so the reviewer can make a defensible decision."
    )

    col_refresh, col_count = st.columns([1, 5])
    with col_refresh:
        if st.button("🔄 Refresh"):
            st.rerun()

    try:
        resp = api_get("/jobs/review")
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to API.")
        return

    if not resp.ok:
        st.error(f"Could not load review queue ({resp.status_code})")
        return

    queue = resp.json()

    if not queue:
        st.success("✅ Review queue is empty — no documents pending.")
        return

    with col_count:
        st.markdown(f"**{len(queue)} document(s) pending review**")

    for item in queue:
        job_id = str(item["job_id"])
        conf   = item.get("confidence_score")
        conf_str = f"{conf:.2f}" if conf is not None else "?"

        with st.expander(
            f"📄 {item['filename']}  —  score: `{conf_str}`  |  `{job_id[:8]}...`",
            expanded=True,
        ):
            # Metadata strip
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                auth = item.get("authentic")
                a_icon = "✅" if auth is True else ("❌" if auth is False else "❓")
                st.metric("Authentic", f"{a_icon} {'Yes' if auth else 'No' if auth is False else '?'}")
            with m2:
                ac = item.get("auth_confidence")
                st.metric("Auth Confidence", f"{ac:.2f}" if ac else "—")
            with m3:
                st.metric("PII Entities Redacted", item.get("entity_count") or "—")
            with m4:
                st.metric("Score", conf_str)

            pii_types = item.get("pii_types_found")
            if pii_types:
                st.caption(f"PII types: `{pii_types}`")

            error = item.get("error_message")
            if error:
                st.error(f"Error: {error}")

            # Full results including score breakdown
            try:
                res_resp = api_get(f"/jobs/{job_id}/results")
                if res_resp.ok:
                    results = res_resp.json()
                    score_data = results.get("score_breakdown")
                    if score_data:
                        st.markdown("**Score Breakdown** — why this was flagged:")
                        render_score_breakdown(score_data)

                    # Risk flags from extraction
                    extraction = results.get("extraction") or {}
                    risk = extraction.get("risk_flags") or {}
                    notes = risk.get("notes")
                    if notes:
                        st.info(f"**LLM analysis note:** {_md(notes)}")
            except Exception:
                st.caption("Could not load score details.")

            # Redaction preview (collapsed)
            with st.expander("🔒 Redacted Document Preview"):
                try:
                    prev = api_get(f"/jobs/{job_id}/redacted-preview")
                    if prev.ok:
                        data = prev.json()
                        redacted_text = data.get("redacted_text", "")
                        st.markdown(
                            f'<div style="background:#fafafa;border:1px solid #e0e0e0;padding:12px;'
                            f'border-radius:8px;font-family:monospace;font-size:0.80em;'
                            f'max-height:300px;overflow-y:auto;white-space:pre-wrap;line-height:1.6">'
                            f'{colorize_redacted(redacted_text)}</div>',
                            unsafe_allow_html=True,
                        )
                except Exception:
                    st.caption("Preview unavailable.")

            # Decision
            st.markdown("---")
            st.markdown("**Make a decision:**")
            notes_key = f"notes_{job_id}"
            notes_input = st.text_input(
                "Reason (required for rejection, recommended for approval)",
                key=notes_key,
                placeholder="e.g. Income verified against stated employment — 2 overdrafts acceptable given balance trend",
            )

            btn1, btn2, _ = st.columns([1, 1, 4])
            with btn1:
                if st.button("✅ Approve", key=f"approve_{job_id}", type="primary"):
                    r = api_post(
                        f"/jobs/{job_id}/review",
                        json={"decision": "approved", "notes": notes_input or None},
                    )
                    if r.ok:
                        st.success("Approved.")
                        time.sleep(0.8)
                        st.rerun()
                    else:
                        st.error(f"Failed: {r.text}")
            with btn2:
                if st.button("❌ Reject", key=f"reject_{job_id}"):
                    if not notes_input:
                        st.warning("Please provide a reason before rejecting — required for audit trail.")
                    else:
                        r = api_post(
                            f"/jobs/{job_id}/review",
                            json={"decision": "rejected", "notes": notes_input},
                        )
                        if r.ok:
                            st.error("Rejected.")
                            time.sleep(0.8)
                            st.rerun()
                        else:
                            st.error(f"Failed: {r.text}")


# ── Main ──────────────────────────────────────────────────────────────────────

st.title("🛡️ Sentinel — Document Intelligence")

if not check_api():
    st.error(
        "**Cannot reach API at `http://localhost:8000`.**\n\n"
        "Run `docker-compose up` from the project root, wait for all services to start, then refresh."
    )
    st.stop()

tab_upload, tab_review = st.tabs(["📄 Upload & Track", "🔍 Review Queue"])

with tab_upload:
    render_upload_tab()

with tab_review:
    render_review_tab()
